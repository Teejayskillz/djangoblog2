from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views import View
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.apps import apps
from django.core.paginator import Paginator
from django.forms import modelform_factory
from django.contrib import admin
from django.http import HttpResponseForbidden, HttpResponse
from django.utils.text import capfirst


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_active and self.request.user.is_staff


def get_model_from_url(app_label, model_name):
    try:
        model = apps.get_model(app_label, model_name)
        return model
    except LookupError:
        return None


class DashboardView(StaffRequiredMixin, View):
    def get(self, request):
        # Gather basic stats
        Post = apps.get_model('core', 'Post')
        Subscriber = apps.get_model('core', 'Subscriber')
        Media = apps.get_model('core', 'Media')
        Comment = apps.get_model('core', 'Comment')
        stats = {
            'posts': Post.objects.count(),
            'subscribers': Subscriber.objects.count(),
            'media': Media.objects.count(),
            'comments': Comment.objects.count(),
        }
        recent_posts = Post.objects.order_by('-published_date')[:8]
        context = {'stats': stats, 'recent_posts': recent_posts}
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/dashboard.html', context)


def get_app_list_context(request):
    """Build a simplified app_list similar to admin app_list for sidebar."""
    app_list = []
    for app_config in apps.get_app_configs():
        models = []
        for model in app_config.get_models():
            models.append({
                'name': model._meta.verbose_name.title(),
                'model_name': model._meta.model_name,
                'app_label': app_config.label
            })
        if models:
            app_list.append({'name': app_config.verbose_name.title(), 'models': models})
    return {'app_list': app_list}


class ModelListView(StaffRequiredMixin, View):
    def post(self, request, app_label, model_name):
        # Handle bulk actions
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')
        action = request.POST.get('action')
        selected = request.POST.getlist('selected')
        if action and selected:
            modeladmin = admin.site._registry.get(model)
            if modeladmin:
                actions = modeladmin.get_actions(request)
                if action in actions:
                    func, name, desc = actions[action]
                    queryset = model.objects.filter(pk__in=selected)
                    try:
                        func(modeladmin, request, queryset)
                        return redirect(request.path + ('?' + request.META.get('QUERY_STRING','') if request.META.get('QUERY_STRING') else ''))
                    except Exception as e:
                        return HttpResponse(f'Action failed: {e}', status=500)
        return redirect(request.path)

    def get(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)

        # Permission: view
        perm = f"{app_label}.view_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')

        # Use ModelAdmin.get_queryset if available to preserve custom querysets
        modeladmin = admin.site._registry.get(model)
        if modeladmin and hasattr(modeladmin, 'get_queryset'):
            try:
                queryset = modeladmin.get_queryset(request)
            except Exception:
                queryset = model.objects.all()
        else:
            queryset = model.objects.all()

        # Search: prefer ModelAdmin.search_fields
        q = request.GET.get('q')
        if q:
            from django.db.models import Q
            qs = Q()
            if modeladmin and getattr(modeladmin, 'search_fields', None):
                for sf in modeladmin.search_fields:
                    qs |= Q(**{f'{sf}__icontains': q})
            else:
                for f in model._meta.fields:
                    if getattr(f, 'get_internal_type', lambda: '')() in ['CharField', 'TextField']:
                        qs |= Q(**{f'{f.name}__icontains': q})
            queryset = queryset.filter(qs).distinct()

        # ordering: honor admin ordering if provided
        order = request.GET.get('o')
        if order:
            try:
                queryset = queryset.order_by(order)
            except Exception:
                pass
        elif modeladmin and getattr(modeladmin, 'ordering', None):
            try:
                queryset = queryset.order_by(*modeladmin.ordering)
            except Exception:
                pass

        # Ensure deterministic ordering to avoid paginator warnings
        try:
            # Django QuerySet has an .ordered attribute on newer versions
            ordered = getattr(queryset, 'ordered', None)
            if not ordered:
                # fallback: apply PK ordering
                queryset = queryset.order_by(model._meta.pk.name)
        except Exception:
            try:
                queryset = queryset.order_by(model._meta.pk.name)
            except Exception:
                pass

        paginator = Paginator(queryset, 20)
        page = request.GET.get('page')
        page_obj = paginator.get_page(page)

        # get columns: build display_columns to allow callables and fields
        display_columns = []
        if modeladmin and getattr(modeladmin, 'list_display', None):
            raw_list = list(modeladmin.list_display)
        else:
            raw_list = [f.name for f in model._meta.fields if f.name not in ('id','password','content')][:6]

        for item in raw_list:
            col = {'label': None, 'name': None, 'type': 'field'}
            if callable(item):
                # callable provided directly on ModelAdmin class
                name = getattr(item, '__name__', None)
                col.update({'label': getattr(item, 'short_description', name) or name, 'name': name, 'type': 'callable'})
            elif isinstance(item, str):
                # string could be field name or ModelAdmin method/property
                if modeladmin and hasattr(modeladmin, item) and callable(getattr(modeladmin, item)):
                    col.update({'label': getattr(getattr(modeladmin, item), 'short_description', item).capitalize(), 'name': item, 'type': 'callable'})
                else:
                    # treat as model field
                    col.update({'label': item.replace('_', ' ').capitalize(), 'name': item, 'type': 'field'})
            else:
                col.update({'label': str(item), 'name': str(item), 'type': 'field'})
            display_columns.append(col)

        actions = []
        if modeladmin:
            try:
                acts = modeladmin.get_actions(request)
                actions = [(k, v[2]) for k, v in acts.items()]
            except Exception:
                actions = []

        # Map simple list filters (supporting field-name filters)
        filters = []
        if modeladmin:
            for lf in getattr(modeladmin, 'list_filter', ()):
                if isinstance(lf, str):
                    try:
                        vals = list(model.objects.order_by(lf).values_list(lf, flat=True).distinct()[:100])
                        filters.append({'field': lf, 'values': [v for v in vals if v is not None]})
                    except Exception:
                        continue

        model_field_names = [f.name for f in model._meta.fields]
        opts = model._meta
        context = {
            'model': model,
            'opts': opts,
            'app_label': app_label,
            'model_name': model_name,
            'page_obj': page_obj,
            'display_columns': display_columns,
            'q': q,
            'actions': actions,
            'filters': filters,
            'modeladmin': modeladmin,
            'model_field_names': model_field_names,
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/list.html', context)


class ModelCreateView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.add_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')

        modeladmin = admin.site._registry.get(model)
        # Try reuse admin form if available
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request)
            except Exception:
                form_class = modelform_factory(model, fields='__all__')
        else:
            form_class = modelform_factory(model, fields='__all__')

        form = form_class()
        # Inline formsets
        inline_formsets = []
        inline_instances = modeladmin.get_inline_instances(request) if modeladmin else []
        for inline in inline_instances:
            FormSet = inline.get_formset(request)
            prefix = f"{inline.opts.model._meta.model_name}_set"
            inline_formsets.append(FormSet(prefix=prefix))

        # fieldsets and readonly fields
        fieldsets = None
        readonly_fields = ()
        if modeladmin:
            try:
                fieldsets = modeladmin.get_fieldsets(request, None)
            except Exception:
                fieldsets = getattr(modeladmin, 'fieldsets', None)
            try:
                readonly_fields = modeladmin.get_readonly_fields(request, None)
            except Exception:
                readonly_fields = getattr(modeladmin, 'readonly_fields', ())

        opts = model._meta
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'action': 'Add',
            'inline_formsets': inline_formsets,
            'fieldsets': fieldsets,
            'readonly_fields': readonly_fields,
            'object': None,
        }
        # use module-level helper to build app list context
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)

    def post(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.add_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')

        modeladmin = admin.site._registry.get(model)
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request)
            except Exception:
                form_class = modelform_factory(model, fields='__all__')
        else:
            form_class = modelform_factory(model, fields='__all__')

        form = form_class(request.POST, request.FILES)
        # prepare inline formsets
        inline_instances = modeladmin.get_inline_instances(request) if modeladmin else []
        inline_formsets = []
        for inline in inline_instances:
            FormSet = inline.get_formset(request)
            prefix = f"{inline.opts.model._meta.model_name}_set"
            fs = FormSet(request.POST, request.FILES, prefix=prefix)
            inline_formsets.append((inline, fs))

        if form.is_valid() and all(fs.is_valid() for _, fs in inline_formsets):
            # Save object via ModelAdmin if available to preserve hooks
            obj = form.save(commit=False)
            if modeladmin and hasattr(modeladmin, 'save_model'):
                modeladmin.save_model(request, obj, form, False)
            else:
                obj.save()
            # Save inline formsets
            for inline, fs in inline_formsets:
                fs.instance = obj
                fs.save()
            # call save_related if provided
            if modeladmin and hasattr(modeladmin, 'save_related'):
                try:
                    modeladmin.save_related(request, form, [fs for _, fs in inline_formsets], False)
                except Exception:
                    pass
            return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))

        opts = model._meta
        context = {'form': form, 'model': model, 'opts': opts, 'action': 'Add', 'inline_formsets': [fs for _, fs in inline_formsets]}
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)


class ModelUpdateView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')
        obj = get_object_or_404(model, pk=pk)
        modeladmin = admin.site._registry.get(model)
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request, obj)
            except Exception:
                form_class = modelform_factory(model, fields='__all__')
        else:
            form_class = modelform_factory(model, fields='__all__')
        form = form_class(instance=obj)
        # prepare inline formsets
        inline_formsets = []
        inline_instances = modeladmin.get_inline_instances(request, obj) if modeladmin else []
        for inline in inline_instances:
            FormSet = inline.get_formset(request, obj)
            prefix = f"{inline.opts.model._meta.model_name}_set"
            inline_formsets.append(FormSet(instance=obj, prefix=prefix))

        # fieldsets and readonly fields
        fieldsets = None
        readonly_fields = ()
        if modeladmin:
            try:
                fieldsets = modeladmin.get_fieldsets(request, obj)
            except Exception:
                fieldsets = getattr(modeladmin, 'fieldsets', None)
            try:
                readonly_fields = modeladmin.get_readonly_fields(request, obj)
            except Exception:
                readonly_fields = getattr(modeladmin, 'readonly_fields', ())

        opts = model._meta
        context = {'form': form, 'model': model, 'opts': opts, 'object': obj, 'action': 'Edit', 'inline_formsets': inline_formsets, 'fieldsets': fieldsets, 'readonly_fields': readonly_fields}
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)

    def post(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')
        obj = get_object_or_404(model, pk=pk)
        modeladmin = admin.site._registry.get(model)
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request, obj)
            except Exception:
                form_class = modelform_factory(model, fields='__all__')
        else:
            form_class = modelform_factory(model, fields='__all__')
        form = form_class(request.POST, request.FILES, instance=obj)

        inline_instances = modeladmin.get_inline_instances(request, obj) if modeladmin else []
        inline_formsets = []
        for inline in inline_instances:
            FormSet = inline.get_formset(request, obj)
            prefix = f"{inline.opts.model._meta.model_name}_set"
            fs = FormSet(request.POST, request.FILES, instance=obj, prefix=prefix)
            inline_formsets.append((inline, fs))

        if form.is_valid() and all(fs.is_valid() for _, fs in inline_formsets):
            # Save via ModelAdmin.save_model if exists to preserve custom behavior
            if modeladmin and hasattr(modeladmin, 'save_model'):
                modeladmin.save_model(request, form.instance, form, True)
            else:
                form.save()
            # save inlines
            for inline, fs in inline_formsets:
                fs.instance = form.instance
                fs.save()
            if modeladmin and hasattr(modeladmin, 'save_related'):
                try:
                    modeladmin.save_related(request, form, [fs for _, fs in inline_formsets], True)
                except Exception:
                    pass
            return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))
        opts = model._meta
        context = {'form': form, 'model': model, 'opts': opts, 'object': obj, 'action': 'Edit', 'inline_formsets': [fs for _, fs in inline_formsets]}
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)


class ModelDetailView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.view_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')
        obj = get_object_or_404(model, pk=pk)
        modeladmin = admin.site._registry.get(model)
        opts = model._meta
        context = {'model': model, 'opts': opts, 'object': obj, 'modeladmin': modeladmin}
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/detail.html', context)


class ModelDeleteView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.delete_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')
        obj = get_object_or_404(model, pk=pk)
        opts = model._meta
        context = {'object': obj, 'model': model, 'opts': opts}
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/confirm_delete.html', context)

    def post(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.delete_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')
        obj = get_object_or_404(model, pk=pk)
        obj.delete()
        return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))

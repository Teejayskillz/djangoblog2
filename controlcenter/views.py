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
        context.update(self.get_app_list_context(request))
        return render(request, 'controlcenter/dashboard.html', context)

    def get_app_list_context(self, request):
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

        queryset = model.objects.all()
        # basic search across string fields
        q = request.GET.get('q')
        if q:
            from django.db.models import Q
            qs = Q()
            for f in model._meta.fields:
                if getattr(f, 'get_internal_type', lambda: '')() in ['CharField', 'TextField']:
                    qs |= Q(**{f'{f.name}__icontains': q})
            queryset = queryset.filter(qs).distinct()

        # ordering
        order = request.GET.get('o')
        if order:
            queryset = queryset.order_by(order)

        paginator = Paginator(queryset, 20)
        page = request.GET.get('page')
        page_obj = paginator.get_page(page)

        # get columns: pick a set of useful display fields
        display_fields = [f.name for f in model._meta.fields if f.name not in ('id','password','content')][:6]

        modeladmin = admin.site._registry.get(model)
        actions = []
        if modeladmin:
            try:
                acts = modeladmin.get_actions(request)
                actions = [(k, v[2]) for k, v in acts.items()]
            except Exception:
                actions = []

        context = {
            'model': model,
            'app_label': app_label,
            'model_name': model_name,
            'page_obj': page_obj,
            'display_fields': display_fields,
            'q': q,
            'actions': actions,
        }
        context.update(self.get_app_list_context(request))
        return render(request, 'controlcenter/list.html', context)


class ModelCreateView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.add_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')

        # Try reuse admin form if available
        form_class = None
        modeladmin = admin.site._registry.get(model)
        if modeladmin:
            try:
                form_class = modeladmin.get_form(request)
            except Exception:
                form_class = None
        if not form_class:
            form_class = modelform_factory(model, fields='__all__')

        form = form_class()
        context = {'form': form, 'model': model, 'action': 'Add'}
        context.update(self.get_app_list_context(request))
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
        if form.is_valid():
            obj = form.save()
            return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))
        context = {'form': form, 'model': model, 'action': 'Add'}
        context.update(self.get_app_list_context(request))
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
        context = {'form': form, 'model': model, 'object': obj, 'action': 'Edit'}
        context.update(self.get_app_list_context(request))
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
        if form.is_valid():
            form.save()
            return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))
        context = {'form': form, 'model': model, 'object': obj, 'action': 'Edit'}
        context.update(self.get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)


class ModelDeleteView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.delete_{model._meta.model_name}"
        if not request.user.has_perm(perm):
            return HttpResponseForbidden('Insufficient permissions')
        obj = get_object_or_404(model, pk=pk)
        context = {'object': obj, 'model': model}
        context.update(self.get_app_list_context(request))
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

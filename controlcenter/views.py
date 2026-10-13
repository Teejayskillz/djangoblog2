from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.apps import apps
from django.core.paginator import Paginator
from django.forms import modelform_factory
from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponseForbidden, HttpResponse, Http404
from django.utils.text import capfirst
from django.utils import timezone
from django.db.models import Count, Q
import datetime

from .models import PageVisit


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_active and self.request.user.is_staff


def get_model_from_url(app_label, model_name):
    try:
        model = apps.get_model(app_label, model_name)
        return model
    except LookupError:
        return None


def get_model_icon(app_label, model_name):
    """Return appropriate dashicon-like emoji/symbol for WordPress sidebar."""
    icons = {
        'post': '📝',
        'category': '📁',
        'comment': '💬',
        'tag': '🏷️',
        'subscriber': '✉️',
        'media': '🖼️',
        'sportsmatch': '⚽',
        'subscriptionplan': '💳',
        'usersubscription': '⭐',
        'paymenttransaction': '💰',
        'ad': '📢',
        'user': '👤',
        'group': '👥',
        'pagevisit': '📊',
    }
    return icons.get(model_name.lower(), '📄')


def get_app_list_context(request):
    """
    Build structured navigation for the WordPress-style admin sidebar.
    Groups apps and models, filters models by user view permissions,
    and sorts prominent apps (Posts, Sports, Subscriptions, Ads) first.
    """
    app_list = []
    # Primary apps order
    app_order = {'core': 1, 'sports': 2, 'subscriptions': 3, 'ads': 4, 'auth': 5, 'controlcenter': 6}

    for app_config in sorted(apps.get_app_configs(), key=lambda a: app_order.get(a.label, 99)):
        # Skip internal or third party utility apps that have no registered models or should be hidden
        if app_config.label in ('sites', 'contenttypes', 'sessions', 'messages', 'staticfiles', 'taggit', 'django_cleanup', 'whitenoise', 'anymail'):
            continue

        models = []
        for model in app_config.get_models():
            # Check view or change permission
            model_name = model._meta.model_name
            perm_view = f"{app_config.label}.view_{model_name}"
            perm_change = f"{app_config.label}.change_{model_name}"
            if request.user.is_superuser or request.user.has_perm(perm_view) or request.user.has_perm(perm_change):
                models.append({
                    'name': model._meta.verbose_name_plural.title(),
                    'singular_name': model._meta.verbose_name.title(),
                    'model_name': model_name,
                    'app_label': app_config.label,
                    'icon': get_model_icon(app_config.label, model_name),
                })

        if models:
            app_list.append({
                'app_label': app_config.label,
                'name': app_config.verbose_name.title(),
                'models': sorted(models, key=lambda m: m['name']),
            })

    return {'app_list': app_list}


class DashboardView(StaffRequiredMixin, View):
    def get(self, request):
        # Query core models safely
        Post = apps.get_model('core', 'Post')
        Subscriber = apps.get_model('core', 'Subscriber')
        Comment = apps.get_model('core', 'Comment')
        SportsMatch = apps.get_model('sports', 'SportsMatch')
        UserSubscription = apps.get_model('subscriptions', 'UserSubscription')

        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Traffic quick numbers (today)
        today_views = PageVisit.objects.filter(timestamp__gte=today_start).count()
        today_visitors = PageVisit.objects.filter(timestamp__gte=today_start).values('visitor_id').distinct().count()

        stats = {
            'posts': Post.objects.count() if Post else 0,
            'subscribers': Subscriber.objects.count() if Subscriber else 0,
            'comments': Comment.objects.count() if Comment else 0,
            'sports_matches': SportsMatch.objects.count() if SportsMatch else 0,
            'active_vip': UserSubscription.objects.filter(is_active=True, end_date__gt=now).count() if UserSubscription else 0,
            'today_views': today_views,
            'today_visitors': today_visitors,
        }

        recent_posts = Post.objects.order_by('-published_date')[:6] if Post else []
        recent_matches = SportsMatch.objects.order_by('-created_at')[:5] if SportsMatch else []
        recent_visits = PageVisit.objects.order_by('-timestamp')[:8]

        context = {
            'stats': stats,
            'recent_posts': recent_posts,
            'recent_matches': recent_matches,
            'recent_visits': recent_visits,
            'active_menu': 'dashboard',
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/dashboard.html', context)


class AnalyticsView(StaffRequiredMixin, View):
    """
    Dedicated Site Analytics view providing WordPress-style traffic reports:
    Daily trends, unique visitors, top pages, referrers, and device breakdown.
    """
    def get(self, request):
        now = timezone.now()
        range_param = request.GET.get('range', '7d')

        if range_param == 'today':
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_count = 1
            range_label = 'Today'
        elif range_param == '30d':
            start_date = (now - datetime.timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
            days_count = 30
            range_label = 'Last 30 Days'
        else: # default 7d
            start_date = (now - datetime.timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            days_count = 7
            range_label = 'Last 7 Days'

        visits_qs = PageVisit.objects.filter(timestamp__gte=start_date)

        total_views = visits_qs.count()
        unique_visitors = visits_qs.values('visitor_id').distinct().count()

        # Top 10 Pages
        top_pages = (
            visits_qs.values('path')
            .annotate(views=Count('id'), unique_visitors=Count('visitor_id', distinct=True))
            .order_by('-views')[:10]
        )

        # Top Referrers
        top_referrers = (
            visits_qs.exclude(referrer_domain__in=['', 'internal'])
            .values('referrer_domain')
            .annotate(hits=Count('id'))
            .order_by('-hits')[:10]
        )

        # Device Breakdown
        device_breakdown = (
            visits_qs.values('device_type')
            .annotate(count=Count('id'))
            .order_by('-count')
        )

        # Build day-by-day series for chart
        daily_stats = []
        for i in range(days_count - 1, -1, -1):
            day_target = (now - datetime.timedelta(days=i)).date()
            day_start = timezone.make_aware(datetime.datetime.combine(day_target, datetime.time.min))
            day_end = timezone.make_aware(datetime.datetime.combine(day_target, datetime.time.max))

            day_qs = visits_qs.filter(timestamp__range=(day_start, day_end))
            day_views = day_qs.count()
            day_visitors = day_qs.values('visitor_id').distinct().count()

            daily_stats.append({
                'date': day_target.strftime('%b %d'),
                'views': day_views,
                'visitors': day_visitors,
            })

        # Recent 25 raw visits
        recent_visits = visits_qs.order_by('-timestamp')[:25]

        context = {
            'range_param': range_param,
            'range_label': range_label,
            'total_views': total_views,
            'unique_visitors': unique_visitors,
            'top_pages': top_pages,
            'top_referrers': top_referrers,
            'device_breakdown': device_breakdown,
            'daily_stats': daily_stats,
            'recent_visits': recent_visits,
            'active_menu': 'analytics',
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/analytics.html', context)


class ModelListView(StaffRequiredMixin, View):
    def post(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
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
                        messages.success(request, f"Successfully executed '{desc}' on {queryset.count()} item(s).")
                    except Exception as e:
                        messages.error(request, f"Action failed: {e}")
            elif action == 'delete_selected':
                count = model.objects.filter(pk__in=selected).delete()[0]
                messages.success(request, f"Deleted {count} item(s).")

        query_string = request.META.get('QUERY_STRING', '')
        redirect_url = request.path + (f'?{query_string}' if query_string else '')
        return redirect(redirect_url)

    def get(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)

        perm_view = f"{app_label}.view_{model._meta.model_name}"
        perm_change = f"{app_label}.change_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm_view) or request.user.has_perm(perm_change)):
            return HttpResponseForbidden('Insufficient permissions')

        modeladmin = admin.site._registry.get(model)
        if modeladmin and hasattr(modeladmin, 'get_queryset'):
            try:
                queryset = modeladmin.get_queryset(request)
            except Exception:
                queryset = model.objects.all()
        else:
            queryset = model.objects.all()

        # Search
        q = request.GET.get('q', '').strip()
        if q:
            search_query = Q()
            if modeladmin and getattr(modeladmin, 'search_fields', None):
                for sf in modeladmin.search_fields:
                    search_query |= Q(**{f'{sf}__icontains': q})
            else:
                for f in model._meta.fields:
                    if getattr(f, 'get_internal_type', lambda: '')() in ['CharField', 'TextField']:
                        search_query |= Q(**{f'{f.name}__icontains': q})
            queryset = queryset.filter(search_query).distinct()

        # Filtering by exact params
        active_filters = {}
        for key, value in request.GET.items():
            if key not in ('q', 'o', 'page') and value:
                try:
                    queryset = queryset.filter(**{key: value})
                    active_filters[key] = value
                except Exception:
                    pass

        # Ordering
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

        # Ensure deterministic ordering for pagination
        try:
            if not getattr(queryset, 'ordered', False):
                queryset = queryset.order_by(f"-{model._meta.pk.name}")
        except Exception:
            pass

        paginator = Paginator(queryset, 20)
        page = request.GET.get('page')
        page_obj = paginator.get_page(page)

        # Build list_display columns
        display_columns = []
        if modeladmin and getattr(modeladmin, 'list_display', None):
            raw_list = list(modeladmin.list_display)
        else:
            raw_list = [f.name for f in model._meta.fields if f.name not in ('id', 'password', 'content')][:6]

        for item in raw_list:
            col = {'label': None, 'name': None, 'type': 'field'}
            if callable(item):
                name = getattr(item, '__name__', 'custom')
                label = getattr(item, 'short_description', name.replace('_', ' ').title())
                col.update({'label': label, 'name': name, 'type': 'callable'})
            elif isinstance(item, str):
                if modeladmin and hasattr(modeladmin, item) and callable(getattr(modeladmin, item)):
                    attr_func = getattr(modeladmin, item)
                    label = getattr(attr_func, 'short_description', item.replace('_', ' ').title())
                    col.update({'label': label, 'name': item, 'type': 'callable'})
                else:
                    col.update({'label': item.replace('_', ' ').title(), 'name': item, 'type': 'field'})
            else:
                col.update({'label': str(item).title(), 'name': str(item), 'type': 'field'})
            display_columns.append(col)

        # Available bulk actions
        actions = []
        if modeladmin:
            try:
                acts = modeladmin.get_actions(request)
                actions = [(k, v[2]) for k, v in acts.items()]
            except Exception:
                actions = []
        if not actions:
            actions = [('delete_selected', 'Delete selected items')]

        # Build filter options for sidebar/dropdowns
        filter_specs = []
        if modeladmin:
            for lf in getattr(modeladmin, 'list_filter', ()):
                if isinstance(lf, str):
                    try:
                        vals = list(model.objects.order_by(lf).values_list(lf, flat=True).distinct()[:50])
                        filter_specs.append({
                            'field': lf,
                            'title': lf.replace('_', ' ').title(),
                            'values': [v for v in vals if v is not None and v != ''],
                            'current': request.GET.get(lf, '')
                        })
                    except Exception:
                        continue

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
            'filter_specs': filter_specs,
            'active_filters': active_filters,
            'modeladmin': modeladmin,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/list.html', context)


class ModelCreateView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.add_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        modeladmin = admin.site._registry.get(model)
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
        if modeladmin:
            for inline in modeladmin.get_inline_instances(request):
                try:
                    FormSet = inline.get_formset(request)
                    prefix = f"{inline.opts.model._meta.model_name}_set"
                    fs = FormSet(prefix=prefix)
                    inline_formsets.append({
                        'title': inline.verbose_name_plural.title() if hasattr(inline, 'verbose_name_plural') else inline.opts.model._meta.verbose_name_plural.title(),
                        'verbose_name': inline.verbose_name.title() if hasattr(inline, 'verbose_name') else inline.opts.model._meta.verbose_name.title(),
                        'formset': fs,
                        'prefix': prefix,
                    })
                except Exception:
                    pass

        opts = model._meta
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'action': 'Add New',
            'inline_formsets': inline_formsets,
            'object': None,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)

    def post(self, request, app_label, model_name):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.add_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
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
        inline_formsets = []
        inline_instances = modeladmin.get_inline_instances(request) if modeladmin else []
        for inline in inline_instances:
            try:
                FormSet = inline.get_formset(request)
                prefix = f"{inline.opts.model._meta.model_name}_set"
                fs = FormSet(request.POST, request.FILES, prefix=prefix)
                inline_formsets.append((inline, fs))
            except Exception:
                pass

        if form.is_valid() and all(fs.is_valid() for _, fs in inline_formsets):
            obj = form.save(commit=False)
            if modeladmin and hasattr(modeladmin, 'save_model'):
                modeladmin.save_model(request, obj, form, False)
            else:
                obj.save()
            form.save_m2m()

            for inline, fs in inline_formsets:
                fs.instance = obj
                fs.save()

            if modeladmin and hasattr(modeladmin, 'save_related'):
                try:
                    modeladmin.save_related(request, form, [fs for _, fs in inline_formsets], False)
                except Exception:
                    pass

            messages.success(request, f"{model._meta.verbose_name.title()} was created successfully.")
            return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))

        opts = model._meta
        rendered_inlines = []
        for inline, fs in inline_formsets:
            rendered_inlines.append({
                'title': inline.verbose_name_plural.title() if hasattr(inline, 'verbose_name_plural') else inline.opts.model._meta.verbose_name_plural.title(),
                'verbose_name': inline.verbose_name.title() if hasattr(inline, 'verbose_name') else inline.opts.model._meta.verbose_name.title(),
                'formset': fs,
                'prefix': fs.prefix,
            })
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'action': 'Add New',
            'inline_formsets': rendered_inlines,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)


class ModelUpdateView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
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
        inline_formsets = []
        if modeladmin:
            for inline in modeladmin.get_inline_instances(request, obj):
                try:
                    FormSet = inline.get_formset(request, obj)
                    prefix = f"{inline.opts.model._meta.model_name}_set"
                    fs = FormSet(instance=obj, prefix=prefix)
                    inline_formsets.append({
                        'title': inline.verbose_name_plural.title() if hasattr(inline, 'verbose_name_plural') else inline.opts.model._meta.verbose_name_plural.title(),
                        'verbose_name': inline.verbose_name.title() if hasattr(inline, 'verbose_name') else inline.opts.model._meta.verbose_name.title(),
                        'formset': fs,
                        'prefix': prefix,
                    })
                except Exception:
                    pass

        opts = model._meta
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'object': obj,
            'action': 'Edit',
            'inline_formsets': inline_formsets,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)

    def post(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.change_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
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
        inline_formsets = []
        inline_instances = modeladmin.get_inline_instances(request, obj) if modeladmin else []
        for inline in inline_instances:
            try:
                FormSet = inline.get_formset(request, obj)
                prefix = f"{inline.opts.model._meta.model_name}_set"
                fs = FormSet(request.POST, request.FILES, instance=obj, prefix=prefix)
                inline_formsets.append((inline, fs))
            except Exception:
                pass

        if form.is_valid() and all(fs.is_valid() for _, fs in inline_formsets):
            if modeladmin and hasattr(modeladmin, 'save_model'):
                modeladmin.save_model(request, form.instance, form, True)
            else:
                form.save()

            for inline, fs in inline_formsets:
                fs.instance = form.instance
                fs.save()

            if modeladmin and hasattr(modeladmin, 'save_related'):
                try:
                    modeladmin.save_related(request, form, [fs for _, fs in inline_formsets], True)
                except Exception:
                    pass

            messages.success(request, f"{model._meta.verbose_name.title()} was updated successfully.")
            return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))

        opts = model._meta
        rendered_inlines = []
        for inline, fs in inline_formsets:
            rendered_inlines.append({
                'title': inline.verbose_name_plural.title() if hasattr(inline, 'verbose_name_plural') else inline.opts.model._meta.verbose_name_plural.title(),
                'verbose_name': inline.verbose_name.title() if hasattr(inline, 'verbose_name') else inline.opts.model._meta.verbose_name.title(),
                'formset': fs,
                'prefix': fs.prefix,
            })
        context = {
            'form': form,
            'model': model,
            'opts': opts,
            'object': obj,
            'action': 'Edit',
            'inline_formsets': rendered_inlines,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/form.html', context)


class ModelDetailView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.view_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        obj = get_object_or_404(model, pk=pk)
        modeladmin = admin.site._registry.get(model)
        opts = model._meta

        fields_data = []
        for f in model._meta.fields:
            val = getattr(obj, f.name, '')
            fields_data.append({
                'label': f.verbose_name.title(),
                'value': val,
                'name': f.name,
            })

        context = {
            'model': model,
            'opts': opts,
            'object': obj,
            'fields_data': fields_data,
            'modeladmin': modeladmin,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/detail.html', context)


class ModelDeleteView(StaffRequiredMixin, View):
    def get(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.delete_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        obj = get_object_or_404(model, pk=pk)
        opts = model._meta
        context = {
            'object': obj,
            'model': model,
            'opts': opts,
            'active_menu': f"{app_label}_{model_name}",
        }
        context.update(get_app_list_context(request))
        return render(request, 'controlcenter/confirm_delete.html', context)

    def post(self, request, app_label, model_name, pk):
        model = get_model_from_url(app_label, model_name)
        if not model:
            return HttpResponse('Model not found', status=404)
        perm = f"{app_label}.delete_{model._meta.model_name}"
        if not (request.user.is_superuser or request.user.has_perm(perm)):
            return HttpResponseForbidden('Insufficient permissions')

        obj = get_object_or_404(model, pk=pk)
        name = str(obj)
        obj.delete()
        messages.success(request, f"Successfully deleted '{name}'.")
        return redirect(reverse('controlcenter:model_list', args=[app_label, model_name]))


class LogoutStaffView(View):
    """Logs out staff user and redirects to login."""
    def get(self, request):
        from django.contrib.auth import logout
        logout(request)
        return redirect('/accounts/login/?next=/control-center/')

    def post(self, request):
        from django.contrib.auth import logout
        logout(request)
        return redirect('/accounts/login/?next=/control-center/')

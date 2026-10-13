from django import template
from django.utils.safestring import mark_safe
from django.utils.html import format_html, escape
import datetime

register = template.Library()

@register.filter
def attr(obj, name):
    """Safely get an attribute, property, or dict key from obj."""
    if obj is None or not name:
        return ''
    try:
        val = getattr(obj, name, None)
        if val is None and hasattr(obj, '__dict__'):
            val = obj.__dict__.get(name, '')
        if callable(val):
            try:
                val = val()
            except Exception:
                pass
        return val
    except Exception:
        return ''

@register.filter
def getitem(obj, key):
    """Return obj[key] for BoundField lookup or dicts."""
    if obj is None or key is None:
        return None
    try:
        return obj[key]
    except Exception:
        try:
            return getattr(obj, key, None)
        except Exception:
            return None

@register.simple_tag
def render_list_column(col, obj, modeladmin=None):
    """
    Renders a column value for ModelListView.
    Handles fields, callables, model methods, booleans, dates, thumbnails safely.
    """
    col_name = col.get('name')
    col_type = col.get('type')

    val = None
    # 1. If callable or ModelAdmin method
    if col_type == 'callable' and modeladmin:
        fn = getattr(modeladmin, col_name, None)
        if callable(fn):
            try:
                val = fn(obj)
            except Exception:
                val = ''
    elif hasattr(obj, col_name) and callable(getattr(obj, col_name)):
        try:
            val = getattr(obj, col_name)()
        except Exception:
            val = ''
    else:
        # Standard field or property
        try:
            val = getattr(obj, col_name, None)
        except Exception:
            val = ''

    if val is None:
        return mark_safe('<span class="text-muted">—</span>')

    # Boolean formatting (WordPress checkmark/dash)
    if isinstance(val, bool):
        if val:
            return mark_safe('<span class="dashicons dashicons-yes-alt text-success" title="Yes">✔</span>')
        return mark_safe('<span class="dashicons dashicons-dismiss text-muted" title="No">—</span>')

    # Date formatting
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.strftime('%b %d, %Y %H:%M') if isinstance(val, datetime.datetime) else val.strftime('%b %d, %Y')

    # Image/FileField formatting
    if hasattr(val, 'url'):
        try:
            url = val.url
            if any(url.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                return mark_safe(f'<img src="{escape(url)}" style="max-height:40px; max-width:60px; border-radius:3px; object-fit:cover;" />')
            return mark_safe(f'<a href="{escape(url)}" target="_blank">View File</a>')
        except Exception:
            pass

    return mark_safe(val) if isinstance(val, str) and ('<' in val and '>' in val) else str(val)


@register.simple_tag
def admin_display(modeladmin, method_name, obj):
    """Legacy helper maintained for backward compatibility."""
    if not modeladmin or not method_name:
        return ''
    fn = getattr(modeladmin, method_name, None)
    if not fn:
        return ''
    try:
        val = fn(obj)
        return mark_safe(val) if isinstance(val, str) else val
    except Exception:
        return ''

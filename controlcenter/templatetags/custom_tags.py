from django import template
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter
def attr(obj, name):
    try:
        return getattr(obj, name)
    except Exception:
        try:
            return obj.__dict__.get(name, '')
        except Exception:
            return ''

@register.simple_tag
def admin_display(modeladmin, method_name, obj):
    """Call a ModelAdmin method (like get_thumbnail_preview) passing the object and return safe string."""
    if not modeladmin:
        return ''
    fn = getattr(modeladmin, method_name, None)
    if not fn:
        return ''
    try:
        val = fn(obj)
        return mark_safe(val) if isinstance(val, str) else val
    except Exception:
        return ''

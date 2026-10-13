from django import template
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

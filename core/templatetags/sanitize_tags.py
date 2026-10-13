from django import template
import re

register = template.Library()

@register.filter(name='strip_template_tags')
def strip_template_tags(value):
    """
    Remove occurrences of Django template variable/tag markers like {{ ... }} and {% ... %}
    from strings stored in the database so they don't appear raw in rendered pages.
    """
    if not value:
        return value
    s = str(value)
    # Remove {{ ... }} and {% ... %} occurrences
    s = re.sub(r"\{\{.*?\}\}", "", s)
    s = re.sub(r"\{\%.*?\%\}", "", s)
    return s.strip()
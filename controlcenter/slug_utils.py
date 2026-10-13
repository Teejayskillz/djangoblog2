"""
Slug auto-generation helpers for the Control Center.

In the Django admin, slug fields are filled from a source field using
``ModelAdmin.prepopulated_fields`` plus ``admin/js/prepopulate.js``. That
JavaScript is injected by the admin's own change-form template, which the
Control Center does not use -- so its custom forms never triggered
auto-generation and an empty slug box failed validation with
"This field is required" (every SlugField in this project is required).

These helpers give the Control Center the same behaviour:

* :func:`get_slug_sources` works out which field feeds which slug field, using
  the ModelAdmin's ``prepopulated_fields`` when available and a sensible
  fallback otherwise.
* :func:`build_unique_slug` builds a length-safe, unique slug server side.
* :func:`apply_slug_defaults` fills blank slugs into a copy of the submitted
  data, so saving keeps working even with JavaScript disabled.
"""

from django.db import models
from django.utils.text import slugify

# Candidate source fields, in preference order, used when a model has a
# conventional ``slug`` field but its ModelAdmin declares no prepopulated fields.
FALLBACK_SOURCE_FIELDS = ('title', 'name', 'headline', 'label')

DEFAULT_SLUG = 'item'


def _fields_by_name(model):
    return {field.name: field for field in model._meta.fields}


def get_slug_sources(model, modeladmin=None, request=None, obj=None):
    """
    Return ``{slug_field_name: source_field_name}`` for ``model``.

    ``prepopulated_fields`` from the registered ModelAdmin wins -- that is the
    same configuration the Django admin honours. Any remaining conventional
    ``slug`` field falls back to the first available field from
    ``FALLBACK_SOURCE_FIELDS``.
    """
    fields = _fields_by_name(model)
    slug_fields = [name for name, field in fields.items() if isinstance(field, models.SlugField)]
    if not slug_fields:
        return {}

    declared = {}
    if modeladmin is not None:
        getter = getattr(modeladmin, 'get_prepopulated_fields', None)
        try:
            declared = (getter(request, obj) if callable(getter)
                        else getattr(modeladmin, 'prepopulated_fields', {})) or {}
        except Exception:
            declared = getattr(modeladmin, 'prepopulated_fields', {}) or {}

    sources = {}
    for target, candidates in declared.items():
        if target not in fields:
            continue
        if isinstance(candidates, str):
            candidates = (candidates,)
        for candidate in candidates or ():
            if candidate in fields:
                sources[target] = candidate
                break

    # Convention-based fallback (e.g. a model whose ModelAdmin declares nothing).
    for name in slug_fields:
        if name in sources or name != 'slug':
            continue
        for candidate in FALLBACK_SOURCE_FIELDS:
            if candidate in fields:
                sources[name] = candidate
                break

    return sources


def build_unique_slug(model, field_name, value, instance=None):
    """
    Slugify ``value`` for ``model.field_name``, keeping it within the field's
    ``max_length`` and appending ``-2``, ``-3`` ... until it is unique.
    """
    field = model._meta.get_field(field_name)
    max_length = getattr(field, 'max_length', None) or 200

    base = slugify(value)[:max_length] or slugify(value, allow_unicode=True)[:max_length]
    if not base:
        base = DEFAULT_SLUG

    queryset = model._default_manager.all()
    if instance is not None and instance.pk:
        queryset = queryset.exclude(pk=instance.pk)

    candidate = base
    counter = 2
    while queryset.filter(**{field_name: candidate}).exists():
        suffix = f"-{counter}"
        candidate = f"{base[:max_length - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def apply_slug_defaults(model, data, modeladmin=None, request=None, instance=None):
    """
    Return a copy of bound form ``data`` with every auto-generated slug filled
    in when the submitted value is blank. Manually entered slugs are untouched.
    """
    sources = get_slug_sources(model, modeladmin=modeladmin, request=request, obj=instance)
    if not sources:
        return data

    if hasattr(data, 'copy'):
        data = data.copy()

    for target, source in sources.items():
        current = data.get(target)
        if isinstance(current, str) and current.strip():
            continue
        source_value = data.get(source)
        if not isinstance(source_value, str) or not source_value.strip():
            continue
        data[target] = build_unique_slug(model, target, source_value, instance=instance)

    return data

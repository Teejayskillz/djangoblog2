#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'blog_project.settings')

    # Test-only compatibility shim (Python 3.14 + Django 5.0 test client).
    # Imported before any test module so collection order cannot matter; never
    # used by runserver/migrate, and a no-op on Python 3.11.
    if 'test' in sys.argv:
        try:
            import nzd_test_compat  # noqa: F401
        except ImportError:
            pass

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()

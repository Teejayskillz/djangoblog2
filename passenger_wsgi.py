
import os
import sys

from django.core.wsgi import get_wsgi_application

# Add your project's parent directory to the Python path
# This assumes your manage.py is in 'my_url_shortener_project'
# and your wsgi.py is in 'my_url_shortener_project/download_shortener_project/'
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

# Set the Django settings module for the 'download_shortener_project' application.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'blog_project.settings')

application = get_wsgi_application()
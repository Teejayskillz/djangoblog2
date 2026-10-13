from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.template.context import BaseContext
from controlcenter.models import PageVisit

# Compatibility patch for Python 3.14 test client template context copying
def _basecontext_copy(self):
    duplicate = self.__class__.__new__(self.__class__)
    duplicate.dicts = self.dicts[:]
    return duplicate
BaseContext.__copy__ = _basecontext_copy

User = get_user_model()


class ControlCenterTests(TestCase):
    def setUp(self):
        self.client = Client()
        # Create regular user and superuser
        self.regular_user = User.objects.create_user(username='regular', email='reg@example.com', password='pass')
        self.admin_user = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass')

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse('controlcenter:dashboard'))
        self.assertIn(resp.status_code, (302, 301))

    def test_dashboard_requires_staff(self):
        self.client.force_login(self.regular_user)
        resp = self.client.get(reverse('controlcenter:dashboard'))
        self.assertIn(resp.status_code, (302, 403))

    def test_dashboard_for_staff(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Control Center')
        self.assertContains(resp, 'Dashboard')
        self.assertContains(resp, 'Site Analytics')

    def test_analytics_view_for_staff(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:analytics'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Site Analytics')
        self.assertContains(resp, 'Total Page Views')
        self.assertContains(resp, 'Unique Visitors')

    def test_model_list_post(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:model_list', args=['core', 'post']))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Posts')
        self.assertContains(resp, 'Bulk Actions')

    def test_model_add_page(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:model_add', args=['core', 'post']))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Add New Post')

    def test_traffic_analytics_middleware_tracking(self):
        # Public visitor requests homepage
        anon_client = Client()
        resp = anon_client.get('/')
        self.assertEqual(resp.status_code, 200)

        # Confirm PageVisit was recorded
        visits = PageVisit.objects.filter(path='/')
        self.assertTrue(visits.exists())
        visit = visits.first()
        self.assertEqual(visit.path, '/')
        self.assertFalse(visit.is_staff_user)

    def test_traffic_analytics_middleware_ignores_controlcenter(self):
        self.client.force_login(self.admin_user)
        self.client.get(reverse('controlcenter:dashboard'))
        
        # Verify /control-center/ was NOT recorded in public traffic analytics
        self.assertFalse(PageVisit.objects.filter(path__startswith='/control-center/').exists())

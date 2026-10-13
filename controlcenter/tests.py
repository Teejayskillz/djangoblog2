from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()

class ControlCenterSmokeTests(TestCase):
    def setUp(self):
        self.client = Client()
        # create a superuser for testing all permissions
        self.admin_user = User.objects.create_superuser(username='admin', email='admin@example.com', password='pass')

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse('controlcenter:dashboard'))
        # should redirect to login
        self.assertIn(resp.status_code, (302, 301))

    def test_dashboard_for_staff(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Dashboard')

    def test_model_list_post(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:model_list', args=['core', 'post']))
        # either 200 or 404 if model not registered; ensure no server error
        self.assertIn(resp.status_code, (200, 404))

    def test_model_add_page(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('controlcenter:model_add', args=['core', 'post']))
        self.assertIn(resp.status_code, (200, 404))

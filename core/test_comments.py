"""
Comment system tests: public reading, authenticated posting, validation, XSS,
premium badges, moderation and anti-spam.
"""

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

import core.testing  # noqa: F401  (applies the Python 3.14 test-client patch)
from core.models import Comment
from core.testing import give_subscription, make_comment, make_post, make_user


class CommentTestBase(TestCase):
    """
    Base for comment behaviour tests.

    The signed timing token and the per-user rate limiter are covered directly
    in controlcenter/test_security.py; here they are stubbed out so each test
    exercises the comment logic itself instead of waiting `FORM_MIN_SUBMIT_SECONDS`.
    """

    def setUp(self):
        cache.clear()
        self._token_patcher = mock.patch(
            'core.views.validate_form_token', return_value=(True, '')
        )
        self._token_patcher.start()
        self.addCleanup(self._token_patcher.stop)

        self.author = make_user('author')
        self.post = make_post()
        self.url = self.post.get_absolute_url()
        self.reader = make_user('reader')
        self.client.force_login(self.reader)

    def login(self, user=None):
        self.client.force_login(user or self.reader)

    def logout(self):
        self.client.logout()


class PublicCommentReadingTests(CommentTestBase):
    def setUp(self):
        super().setUp()
        self.logout()

    def test_logged_out_visitors_can_read_approved_comments(self):
        make_comment(self.post, self.reader, text='Loved this movie!')

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Loved this movie!')
        self.assertContains(response, self.reader.username)
        # Comments are server-rendered, not fetched by JavaScript.
        self.assertContains(response, 'comment-list')

    def test_comments_are_visible_in_the_initial_html_response(self):
        make_comment(self.post, self.reader, text='Server rendered comment')

        html = self.client.get(self.url).content.decode()

        self.assertIn('Server rendered comment', html)
        self.assertNotIn('comments?format=json', html)

    def test_total_comment_count_is_shown(self):
        make_comment(self.post, self.reader, text='One')
        make_comment(self.post, self.reader, text='Two')

        response = self.client.get(self.url)

        self.assertContains(response, '2 comments')

    def test_unapproved_comments_are_not_public(self):
        make_comment(self.post, self.reader, text='Hidden pending comment', approved=False)

        response = self.client.get(self.url)

        self.assertNotContains(response, 'Hidden pending comment')
        self.assertContains(response, '0 comments')

    def test_empty_state_is_shown_when_there_are_no_comments(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'No comments yet')

    def test_logged_out_visitor_sees_login_and_register_calls_to_action(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'Log in to comment')
        self.assertContains(response, 'Create an account')
        self.assertContains(response, f'{reverse("login")}?next={self.url}')


class CommentSubmissionTests(CommentTestBase):
    def test_logged_out_visitor_cannot_post_a_comment(self):
        self.logout()

        response = self.client.post(self.url, {'comment': 'Sneaky anonymous comment'})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith(reverse('login')))
        self.assertIn(f'next={self.url}', response['Location'])
        self.assertEqual(Comment.objects.count(), 0)

    def test_logged_out_post_redirect_returns_to_the_post(self):
        self.logout()

        response = self.client.post(self.url, {'comment': 'Try again'}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please log in to join the discussion.')

    def test_logged_in_user_can_submit_a_comment(self):
        response = self.client.post(self.url, {'comment': '  Great movie, thanks!  '})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].endswith(f'{self.url}#comments'))
        comment = Comment.objects.get()
        self.assertEqual(comment.user, self.reader)
        self.assertEqual(comment.comment, 'Great movie, thanks!')
        self.assertEqual(comment.post, self.post)
        # Moderation: new comments are not public until approved.
        self.assertFalse(comment.is_approved)

    def test_submitted_comment_is_announced_as_awaiting_moderation(self):
        response = self.client.post(self.url, {'comment': 'Pending approval'}, follow=True)

        self.assertContains(response, 'will appear once a moderator approves it')
        self.assertNotContains(response, 'Pending approval</p>')

    def test_author_details_come_from_the_account_not_the_request(self):
        self.client.post(self.url, {
            'comment': 'Impersonation attempt',
            'name': 'Administrator',
            'email': 'admin@example.com',
            'user': self.author.pk,
        })

        comment = Comment.objects.get()
        self.assertEqual(comment.user, self.reader)
        self.assertNotEqual(comment.name, 'Administrator')

    def test_whitespace_is_trimmed(self):
        self.client.post(self.url, {'comment': '\n\n   Trimmed comment   \n\n'})

        self.assertEqual(Comment.objects.get().comment, 'Trimmed comment')

    def test_blank_comment_is_rejected(self):
        response = self.client.post(self.url, {'comment': '     '})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertContains(response, 'Please write a comment')

    def test_comment_shorter_than_minimum_is_rejected(self):
        response = self.client.post(self.url, {'comment': 'ok'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Comment.objects.count(), 0)

    def test_excessively_long_comment_is_rejected(self):
        response = self.client.post(self.url, {'comment': 'x' * 2001})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertContains(response, 'cannot be longer than')

    def test_comment_form_input_is_preserved_when_validation_fails(self):
        long_text = 'preserved text ' + ('y' * 2000)
        response = self.client.post(self.url, {'comment': long_text})

        self.assertContains(response, 'preserved text')

    def test_honeypot_field_rejects_bots(self):
        response = self.client.post(self.url, {
            'comment': 'Spammy bot comment',
            'website': 'http://spam.example.com',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Comment.objects.count(), 0)

    @override_settings(COMMENT_MIN_INTERVAL_SECONDS=0, COMMENT_DUPLICATE_WINDOW_MINUTES=10)
    def test_identical_comment_is_rejected_within_the_window(self):
        first = self.client.post(self.url, {'comment': 'Same thing twice'})
        self.assertEqual(first.status_code, 302)

        second = self.client.post(self.url, {'comment': 'Same thing twice'})

        self.assertEqual(second.status_code, 200)
        self.assertContains(second, 'already posted that comment')
        self.assertEqual(Comment.objects.count(), 1)

    def test_rapid_posting_is_rate_limited(self):
        self.client.post(self.url, {'comment': 'First comment here'})

        response = self.client.post(self.url, {'comment': 'Second comment immediately'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'commenting too quickly')
        self.assertEqual(Comment.objects.count(), 1)

    def test_staff_are_exempt_from_rate_limiting(self):
        moderator = make_user('moderator')
        moderator.is_staff = True
        moderator.save(update_fields=['is_staff'])
        self.login(moderator)

        self.client.post(self.url, {'comment': 'First staff comment'})
        self.client.post(self.url, {'comment': 'Second staff comment'})

        self.assertEqual(Comment.objects.filter(user=moderator).count(), 2)


class CommentXssTests(CommentTestBase):
    def test_submitted_html_is_escaped_not_rendered(self):
        payload = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
        self.client.post(self.url, {'comment': payload})
        Comment.objects.update(is_approved=True)

        response = self.client.get(self.url)
        html = response.content.decode()

        self.assertNotIn('<script>alert("xss")</script>', html)
        self.assertNotIn('<img src=x onerror', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;img', html)

    def test_quotes_are_escaped_in_comment_output(self):
        self.client.post(self.url, {'comment': 'He said "hello" & <b>bye</b>'})
        Comment.objects.update(is_approved=True)

        html = self.client.get(self.url).content.decode()

        self.assertIn('&quot;hello&quot;', html)
        self.assertIn('&amp;', html)
        self.assertIn('&lt;b&gt;bye&lt;/b&gt;', html)


class PremiumBadgeTests(CommentTestBase):
    def badge_shown_for(self, user):
        make_comment(self.post, user, text=f'Comment from {user.username}')
        return 'VIP subscriber' in self.client.get(self.url).content.decode()

    def test_premium_subscriber_gets_the_badge(self):
        premium = make_user('premium')
        give_subscription(premium, days=30)

        self.assertTrue(self.badge_shown_for(premium))

    def test_ordinary_user_gets_no_badge(self):
        self.assertFalse(self.badge_shown_for(make_user('ordinary')))

    def test_expired_subscription_loses_the_badge(self):
        expired = make_user('expired')
        give_subscription(expired, days=-1)

        self.assertFalse(self.badge_shown_for(expired))

    def test_inactive_subscription_loses_the_badge(self):
        revoked = make_user('revoked')
        give_subscription(revoked, days=30, is_active=False)

        self.assertFalse(self.badge_shown_for(revoked))

    def test_badge_disappears_once_the_subscription_expires(self):
        member = make_user('member')
        subscription = give_subscription(member, days=30)
        make_comment(self.post, member)

        self.assertIn('VIP subscriber', self.client.get(self.url).content.decode())

        # Expire it the way the site does: the end date passes.
        from django.utils import timezone
        from datetime import timedelta

        subscription.end_date = timezone.now() - timedelta(seconds=1)
        subscription.save(update_fields=['end_date'])

        self.assertNotIn('VIP subscriber', self.client.get(self.url).content.decode())

    def test_badge_is_accessible_and_labelled(self):
        premium = make_user('vipuser')
        give_subscription(premium)
        make_comment(self.post, premium)

        html = self.client.get(self.url).content.decode()

        self.assertIn('aria-label="VIP subscriber"', html)
        self.assertIn('title="VIP subscriber"', html)

    def test_badge_cannot_be_requested_by_the_browser(self):
        self.client.post(self.url, {
            'comment': 'I am totally premium',
            'is_premium': '1',
            'premium': 'on',
            'user_id': self.reader.pk,
        })
        Comment.objects.update(is_approved=True)

        html = self.client.get(self.url).content.decode()

        self.assertNotIn('VIP subscriber', html)

    def test_staff_owner_accounts_follow_the_existing_entitlement_rule(self):
        # subscriptions.utils.user_has_active_subscription treats superusers as
        # entitled; the badge reuses exactly that rule.
        owner = make_user('owner', is_superuser=True, is_staff=True)

        self.assertTrue(self.badge_shown_for(owner))


class CommentPaginationTests(CommentTestBase):
    def setUp(self):
        super().setUp()
        for index in range(25):
            make_comment(self.post, self.reader, text=f'Comment number {index}')

    def test_first_page_is_paginated(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Comment number 24')
        self.assertNotContains(response, 'Comment number 0')
        self.assertContains(response, 'Page 1 of 2')
        self.assertContains(response, 'comments_page=2')

    def test_second_page_shows_the_remaining_comments(self):
        response = self.client.get(f'{self.url}?comments_page=2')

        self.assertContains(response, 'Comment number 0')
        self.assertNotContains(response, 'Comment number 24')
        self.assertContains(response, 'Page 2 of 2')

    def test_invalid_page_number_falls_back_to_the_last_page(self):
        response = self.client.get(f'{self.url}?comments_page=999')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Comment number 0')


class CommentModerationTests(CommentTestBase):
    def setUp(self):
        super().setUp()
        self.moderator = make_user('moderator', is_staff=True, is_superuser=True)

    def test_moderator_can_open_the_comment_admin(self):
        comment = make_comment(self.post, self.reader, text='Needs review', approved=False)
        self.login(self.moderator)

        changelist = self.client.get('/django-admin-backend-internal/core/comment/')
        self.assertEqual(changelist.status_code, 200)
        self.assertContains(changelist, 'Comments')

        change_form = self.client.get(
            f'/django-admin-backend-internal/core/comment/{comment.pk}/change/'
        )
        self.assertEqual(change_form.status_code, 200)
        self.assertContains(change_form, 'Needs review')
        self.assertContains(change_form, 'id_is_approved')

    def test_approve_and_hide_admin_actions_change_visibility(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        comment = make_comment(self.post, self.reader, text='Action target', approved=False)
        model_admin = django_admin.site._registry[Comment]
        request = RequestFactory().get('/')
        request.user = self.moderator
        request._messages = type(
            'Messages', (), {'add': lambda *a, **k: None, 'success': lambda *a, **k: None}
        )()

        model_admin.approve_comments(request, Comment.objects.filter(pk=comment.pk))
        comment.refresh_from_db()
        self.assertTrue(comment.is_approved)
        self.assertContains(self.client.get(self.url), 'Action target')

        model_admin.hide_comments(request, Comment.objects.filter(pk=comment.pk))
        comment.refresh_from_db()
        self.assertFalse(comment.is_approved)
        self.assertNotContains(self.client.get(self.url), 'Action target')

    def test_ordinary_users_cannot_reach_the_comment_admin(self):
        self.login(make_user('plain'))
        response = self.client.get('/django-admin-backend-internal/core/comment/')
        self.assertIn(response.status_code, (302, 403))

    def test_comment_authorship_cannot_be_edited_through_the_site(self):
        comment = make_comment(self.post, self.reader, text='Mine')

        # There is no public edit/delete endpoint: POSTing to the post URL again
        # only creates a new comment, and never mutates another user's comment.
        self.client.post(self.url, {'comment': 'A new one'})

        comment.refresh_from_db()
        self.assertEqual(comment.comment, 'Mine')
        self.assertFalse(hasattr(comment, 'is_approved_by_user'))

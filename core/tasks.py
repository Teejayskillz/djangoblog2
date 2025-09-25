# blog/tasks.py
from celery import shared_task
from django.core.mail import send_mass_mail
from django.template.loader import render_to_string
from .models import Subscriber, Post  # Import the Post model

@shared_task
def send_new_post_notification(post_id):
    try:
        post = Post.objects.get(id=post_id)
    except Post.DoesNotExist:
        return # Exit if the post doesn't exist

    subject = f'New Blog Post: {post.title}'
    subscribers = Subscriber.objects.all()
    recipient_list = [sub.email for sub in subscribers]

    html_message = render_to_string('blog/email/new_post_notification.html', {'post': post})
    plain_message = render_to_string('blog/email/new_post_notification.txt', {'post': post})

    datatuple = [(subject, plain_message, None, [recipient]) for recipient in recipient_list]

    send_mass_mail(datatuple, fail_silently=False)
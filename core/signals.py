# core/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.urls import reverse
from telegram import Bot, constants
from telegram.error import TelegramError, BadRequest
from telegram.helpers import escape_markdown
from django.contrib.sites.models import Site
import asyncio
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Post

import json
import requests
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)

from .models import Post

@receiver(post_save, sender=Post)
def auto_post_to_telegram(sender, instance, **kwargs):
    logger.info(f"Signal triggered for Post: '{instance.title}', Created: {kwargs.get('created')}, Published: {instance.is_published}")

    if instance.is_published: # Post to Telegram if the post is published (whether new OR updated)
        bot_token = settings.TELEGRAM_BOT_TOKEN
        channel_ids = getattr(settings, 'TELEGRAM_CHANNEL_IDS', [])
        
        if not bot_token:
            logger.error("TELEGRAM_BOT_TOKEN is not defined in settings.py")
            return
        if not channel_ids:
            logger.warning("TELEGRAM_CHANNEL_IDS is empty or not defined in settings.py. No channels to post to.")
            return

        bot = Bot(token=bot_token)

        current_site = Site.objects.get_current()
        post_url = f"http://{current_site.domain}{instance.get_absolute_url()}" 

        escaped_post_url = escape_markdown(post_url, version=2)

        photo_url = None
        if hasattr(instance, 'image') and instance.image and instance.image.url:
            photo_url = f"http://{current_site.domain}{instance.image.url}"
        elif hasattr(instance, 'thumbnail') and instance.thumbnail and instance.thumbnail.url:
            photo_url = f"http://{current_site.domain}{instance.thumbnail.url}"
        
        if not photo_url:
            logger.warning(f"Post '{instance.title}' has no image/thumbnail. Will send a text-only message.")

        escaped_title = escape_markdown(instance.title, version=2)
        
        content_for_message = ""
        if getattr(instance, 'excerpt', None):
            content_for_message = instance.excerpt
        else:
            content_for_message = instance.content

        if len(content_for_message) > 200:
            content_for_message = content_for_message[:200] + "..."
        elif getattr(instance, 'excerpt', None):
            content_for_message += "..."

        escaped_excerpt_or_content = escape_markdown(content_for_message, version=2)

        # --- WhatsApp Channel Information ---
        whatsapp_channel_url = "https://whatsapp.com/channel/0029VaZdUiBEAKWIhQCJTg1d"
        # Escape the WhatsApp URL as it will be displayed directly and contains dots
        escaped_whatsapp_channel_url = escape_markdown(whatsapp_channel_url, version=2)

        # --- CONSTRUCT CAPTION TEXT ---
        caption_text = f"📢 **{escaped_title}**\n\n"
        caption_text += f"{escaped_excerpt_or_content}\n\n"
        caption_text += f"🔗 {escaped_post_url}"
        
        # --- ADDING WHATSAPP LINK BELOW POST URL ---
        # Added two newlines (\n\n) for spacing between the post link and the WhatsApp info
        caption_text += f"\n\nJOIN OUR WHATSAPP MOVIE CHANNEL\n{escaped_whatsapp_channel_url}"

        async def send_telegram_message_async():
            for chat_id in channel_ids:
                try:
                    logger.info(f"Attempting to send '{instance.title}' to Telegram channel: {chat_id}")
                    
                    if photo_url:
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=photo_url,
                            caption=caption_text,
                            parse_mode=constants.ParseMode.MARKDOWN_V2
                        )
                    else:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=caption_text,
                            parse_mode=constants.ParseMode.MARKDOWN_V2
                        )
                    logger.info(f"Successfully posted '{instance.title}' to Telegram channel: {chat_id}")
                except BadRequest as e:
                    logger.error(f"Telegram BadRequest Error for channel {chat_id} while posting '{instance.title}': {e}")
                except TelegramError as e:
                    logger.error(f"Telegram API Error for channel {chat_id} while posting '{instance.title}': {e}")
                except Exception as e:
                    logger.error(f"An unexpected error occurred for channel {chat_id} while posting '{instance.title}' to Telegram: {e}")

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            loop.create_task(send_telegram_message_async())
        else:
            loop.run_until_complete(send_telegram_message_async())
    else:
        logger.info(f"Post '{instance.title}' not published, skipping Telegram post.")

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
import requests
import json
from .models import Post

@receiver(post_save, sender=Post)
def send_post_to_emailhub(sender, instance, created, **kwargs):
    """
    Send post to EmailHub when published and marked for sharing via email.
    Includes thumbnail, styled content, and 'Read More' link with category path.
    """
    if instance.shared_via_email and instance.is_published:
        site_url = getattr(settings, "SITE_URL", "https://nzdworld.com")

        # Build post URL dynamically based on category slug
        if instance.category:
            post_url = f"{site_url}/{instance.category.slug}/{instance.slug}/"
        else:
            post_url = f"{site_url}/{instance.slug}/"

        # Build absolute thumbnail URL
        thumbnail_url = f"{site_url}{instance.thumbnail.url}" if instance.thumbnail else ""

        # Prepare styled HTML content for email
        preview_content = f"""
        <div style="font-family: Arial, Helvetica, sans-serif; background-color:#f9f9f9; padding:20px 0;">
            <div style="max-width:600px; margin:0 auto; background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 4px 10px rgba(0,0,0,0.1);">
                
                <!-- Header -->
                <div style="background-color:#007bff; color:#fff; text-align:center; padding:15px;">
                    <h2 style="margin:0; font-size:20px;">{instance.title}</h2>
                </div>

                <!-- Thumbnail -->
                {'<img src="'+thumbnail_url+'" alt="Thumbnail" style="width:100%; height:auto; display:block;">' if thumbnail_url else ''}

                <!-- Content -->
                <div style="padding:20px; color:#333; font-size:16px; line-height:1.5;">
                    <p>{instance.content[:500]}...</p>
                    <div style="text-align:center; margin-top:20px;">
                        <a href="{post_url}" 
                           style="display:inline-block; background:#007bff; color:#fff; padding:12px 24px; text-decoration:none; border-radius:5px; font-weight:bold;">
                            Read Full Post →
                        </a>
                    </div>
                </div>

                <!-- Footer -->
                <div style="background-color:#f1f1f1; text-align:center; padding:15px; font-size:12px; color:#666;">
                    <p>You're receiving this email because you're subscribed to updates from <strong>NZDWorld</strong>.</p>
                    <p>© 2025 NZDWorld. All rights reserved.</p>
                </div>
            </div>
        </div>
        """

        # Prepare data payload
        data = {
            "title": instance.title,
            "content": preview_content,  # send HTML content with styling
            "thumbnail_url": thumbnail_url,
            "read_more_url": post_url,
        }

        # Send to EmailHub
        try:
            url = "http://mailhub.nzdworld.com/api/receive-post/"
            response = requests.post(
                url,
                data=json.dumps(data),
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            print("✅ Post sent to EmailHub:", response.json())
        except Exception as e:
            print("❌ Error sending to EmailHub:", str(e))

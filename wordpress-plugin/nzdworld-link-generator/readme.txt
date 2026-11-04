=== NZDWorld Link Generator ===
Contributors: nzdworld
Tags: downloads, countdown, nzdworld, link generator, movie downloads
Requires at least: 5.8
Tested up to: 6.6
Requires PHP: 7.4
Stable tag: 1.0.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Receives secure download handoffs from NZDWorld, shows a countdown popup on a random published job article, and continues the download after the countdown.

== Description ==

This plugin is the WordPress half of the **NZDWorld External Link Generator**.

When a visitor clicks a download button on NZDWorld, NZDWorld creates an
opaque, short-lived, single-use token and redirects the visitor to this site.
The plugin then:

1. verifies the token with NZDWorld over HTTPS (signed server-to-server request),
2. picks a random **published** job article (never drafts, private, trashed or
   password-protected posts),
3. redirects the visitor in the same browser tab to that article with an opaque
   `nzd_gen` identifier,
4. opens a polished countdown popup,
5. when the countdown is over and the visitor clicks the button, asks NZDWorld to
   redeem the session and only then continues to the real download link.

The real download URL is never placed in this site's HTML, JavaScript, page
cache or URL before redemption: NZDWorld releases it only to a server-to-server
redeem call after its own deadline has passed.

**Highlights**

* Works with any theme, no page builder, no third-party plugin, no JavaScript
  framework.
* Admin screen with connection test, countdown, article routing, popup text and
  statistics.
* Server-side countdown enforcement: editing the JavaScript cannot release a
  download early, and refreshing the article never restarts the countdown.
* API key stays on the server, is masked in the admin form and is never exposed
  to visitors.
* Sessions are bound to the visitor's browser and cleaned up hourly.

== Installation ==

1. Download `nzdworld-link-generator.zip`.
2. In WordPress go to **Plugins -> Add New -> Upload Plugin**, choose the ZIP and
   click **Install Now**, then **Activate**.
3. In NZDWorld go to **Control Center -> Link Generator**, generate an API key
   and enable the generator.
4. In WordPress go to **NZDWorld Link Generator** and fill in:
   * **NZDWorld website URL** (for example `https://nzdworld.com`),
   * **API key** (paste the key from step 3),
   * **Blog page link** (for example `https://blog.example.com/jobs/`),
5. Click **Save & Test Connection**. The status should become *Connected*.
6. Adjust the countdown, routing and popup text, then save again.

That is all: download buttons on NZDWorld will now route through this site.

== Frequently Asked Questions ==

= Does the plugin need a page builder or a paid plugin? =
No. It is plain PHP, JavaScript and CSS on top of WordPress core APIs.

= Will ordinary visitors see the popup? =
No. The popup is only rendered when the request carries an `nzd_gen`
identifier that matches a live, server-side session for that article and that
browser. Opening a job article normally shows nothing.

= What happens if NZDWorld is down? =
The entry route shows a clear "we could not verify this download" page with a
link back to NZDWorld. The visitor's NZDWorld download link keeps working once
the services can talk to each other again.

= Does refreshing the article restart the countdown? =
No. The deadline (`not_before`) is stored server-side. A refresh re-reads the
remaining seconds, so the countdown continues where it was.

= Can somebody skip the countdown by editing JavaScript? =
No. The popup's countdown is only cosmetic: both this plugin and NZDWorld
refuse to redeem before the server-side deadline.

= Is the API key exposed to visitors? =
No. It is stored in the WordPress options table and only used by the server-side
API client. The admin field is masked and leaving it blank keeps the stored key.

= Why do I see "PHP OpenSSL is missing"? =
The plugin encrypts the NZDWorld session token at rest. Without the OpenSSL
extension it refuses to create sessions. Ask your host to enable the PHP
`openssl` extension.

== Changelog ==

= 1.0.0 =
* Initial release: secure handoff entry route, random published article
  routing, countdown popup, server-to-server redemption, admin settings,
  connection test and statistics.

== Upgrade Notice ==

= 1.0.0 =
Initial release.

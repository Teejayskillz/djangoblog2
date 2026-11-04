<?php
/**
 * Admin settings screen markup.
 *
 * @package NZDWorld_Link_Generator
 *
 * @var array      $settings       Stored settings.
 * @var array      $stats          Statistics counters.
 * @var array      $session_counts Session counts by status.
 * @var array|null $notice         One-time admin notice.
 * @var array      $categories     WordPress categories.
 * @var string     $entry_template Entry URL template reported to NZDWorld.
 * @var bool       $crypto_ok      OpenSSL availability.
 * @var bool       $https_ok       This site is served over HTTPS.
 */

defined( 'ABSPATH' ) || exit;

$nzd_page_url  = add_query_arg( 'page', NZD_LG_Admin::PAGE_SLUG, admin_url( 'admin.php' ) );
$nzd_has_key   = '' !== (string) $settings['api_key'];
$nzd_masked    = NZD_LG_Options::masked_key();
$nzd_status    = (string) $settings['connection_status'];
$nzd_max_ttl   = max( 120, min( 3600, (int) $settings['max_session_seconds'] ) );
$nzd_entry_url = str_replace( '{token}', 'sample-token', $entry_template );
$nzd_perm      = (string) get_option( 'permalink_structure' );
?>
<div class="wrap">
	<h1><?php echo esc_html__( 'NZDWorld Link Generator', 'nzdworld-link-generator' ); ?></h1>
	<p class="description" style="max-width:760px">
		<?php
		echo esc_html__(
			'Hands NZDWorld download clicks to this site: the visitor is verified, sent to a random published job article and sees a countdown popup. The real download link is only released after the countdown, by NZDWorld, server to server.',
			'nzdworld-link-generator'
		);
		?>
	</p>

	<?php if ( is_array( $notice ) && ! empty( $notice['messages'] ) ) : ?>
		<div class="notice notice-<?php echo esc_attr( isset( $notice['type'] ) ? $notice['type'] : 'success' ); ?> is-dismissible">
			<?php foreach ( (array) $notice['messages'] as $nzd_line ) : ?>
				<p><?php echo esc_html( $nzd_line ); ?></p>
			<?php endforeach; ?>
		</div>
	<?php endif; ?>

	<?php if ( ! $crypto_ok ) : ?>
		<div class="notice notice-error">
			<p><strong><?php echo esc_html__( 'PHP OpenSSL is missing.', 'nzdworld-link-generator' ); ?></strong>
				<?php echo esc_html__( 'Secure download sessions cannot be stored or redeemed until your host enables the OpenSSL extension.', 'nzdworld-link-generator' ); ?></p>
		</div>
	<?php endif; ?>

	<?php if ( ! $https_ok ) : ?>
		<div class="notice notice-error">
			<p><strong><?php echo esc_html__( 'This site is not using HTTPS.', 'nzdworld-link-generator' ); ?></strong>
				<?php echo esc_html__( 'The generator refuses to run over plain HTTP because the handoff and the API carry session tokens.', 'nzdworld-link-generator' ); ?></p>
		</div>
	<?php endif; ?>

	<?php if ( '' === $nzd_perm ) : ?>
		<div class="notice notice-warning">
			<p><?php echo esc_html__( 'Pretty permalinks are disabled, so the query-string entry URL is used. That works, but enabling permalinks gives cleaner links.', 'nzdworld-link-generator' ); ?></p>
		</div>
	<?php endif; ?>

	<div class="card" style="max-width:860px;padding:6px 18px 14px;margin-top:16px">
		<h2 style="margin-bottom:6px"><?php echo esc_html__( 'Status', 'nzdworld-link-generator' ); ?></h2>
		<table class="form-table" role="presentation">
			<tr>
				<th scope="row"><?php echo esc_html__( 'Generator', 'nzdworld-link-generator' ); ?></th>
				<td>
					<?php if ( ! empty( $settings['enabled'] ) ) : ?>
						<span style="color:#065f46;font-weight:600">&#10003; <?php echo esc_html__( 'Enabled', 'nzdworld-link-generator' ); ?></span>
					<?php else : ?>
						<span style="color:#92400e;font-weight:600">&#10007; <?php echo esc_html__( 'Disabled', 'nzdworld-link-generator' ); ?></span>
					<?php endif; ?>
					<?php if ( ! $nzd_has_key ) : ?>
						<span style="color:#b91c1c"><?php echo esc_html__( '- no API key stored', 'nzdworld-link-generator' ); ?></span>
					<?php endif; ?>
				</td>
			</tr>
			<tr>
				<th scope="row"><?php echo esc_html__( 'Connection', 'nzdworld-link-generator' ); ?></th>
				<td>
					<?php
					$nzd_status_labels = array(
						'connected' => array( '&#10003; ' . __( 'Connected', 'nzdworld-link-generator' ), '#065f46' ),
						'disabled'  => array( '&#9888; ' . __( 'Connected, generator disabled on NZDWorld', 'nzdworld-link-generator' ), '#92400e' ),
						'error'     => array( '&#10007; ' . __( 'Connection failed', 'nzdworld-link-generator' ), '#b91c1c' ),
						'unknown'   => array( __( 'Not tested yet', 'nzdworld-link-generator' ), '#4b5563' ),
					);
					$nzd_box          = isset( $nzd_status_labels[ $nzd_status ] ) ? $nzd_status_labels[ $nzd_status ] : $nzd_status_labels['unknown'];
					?>
					<span style="color:<?php echo esc_attr( $nzd_box[1] ); ?>;font-weight:600"><?php echo wp_kses_post( $nzd_box[0] ); ?></span>
					<?php if ( '' !== (string) $settings['connection_message'] ) : ?>
						<p class="description"><?php echo esc_html( $settings['connection_message'] ); ?></p>
					<?php endif; ?>
					<?php if ( '' !== (string) $settings['last_success'] ) : ?>
						<p class="description">
							<?php echo esc_html__( 'Last successful connection:', 'nzdworld-link-generator' ); ?>
							<code><?php echo esc_html( $settings['last_success'] ); ?> UTC</code>
						</p>
					<?php endif; ?>
				</td>
			</tr>
			<tr>
				<th scope="row"><?php echo esc_html__( 'Entry URL', 'nzdworld-link-generator' ); ?></th>
				<td>
					<code><?php echo esc_html( $nzd_entry_url ); ?></code>
					<p class="description">
						<?php echo esc_html__( 'NZDWorld receives this automatically when the connection test succeeds. The token is opaque and single-use. Re-run the test after changing permalinks or moving the site.', 'nzdworld-link-generator' ); ?>
					</p>
				</td>
			</tr>
		</table>
	</div>

	<form method="post" action="<?php echo esc_url( admin_url( 'admin-post.php' ) ); ?>">
		<?php wp_nonce_field( 'nzd_lg_save' ); ?>
		<input type="hidden" name="action" value="nzd_lg_save" />

		<div class="card" style="max-width:860px;padding:6px 18px 14px;margin-top:16px">
			<h2 style="margin-bottom:6px"><?php echo esc_html__( 'Connection', 'nzdworld-link-generator' ); ?></h2>
			<table class="form-table" role="presentation">
				<tr>
					<th scope="row"><label for="nzd-lg-enabled"><?php echo esc_html__( 'Enable generator', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<label>
							<input type="checkbox" id="nzd-lg-enabled" name="nzd_lg[enabled]" value="1" <?php checked( 1, (int) $settings['enabled'] ); ?> />
							<?php echo esc_html__( 'Accept download handoffs from NZDWorld', 'nzdworld-link-generator' ); ?>
						</label>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="nzd-lg-nzd-url"><?php echo esc_html__( 'NZDWorld website URL', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<input type="url" class="regular-text" id="nzd-lg-nzd-url" name="nzd_lg[nzd_site_url]"
							value="<?php echo esc_attr( $settings['nzd_site_url'] ); ?>" placeholder="https://nzdworld.com" />
						<p class="description"><?php echo esc_html__( 'HTTPS only, e.g. https://nzdworld.com (no trailing slash needed).', 'nzdworld-link-generator' ); ?></p>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="nzd-lg-api-key"><?php echo esc_html__( 'API key', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<input type="password" class="regular-text" id="nzd-lg-api-key" name="nzd_lg[api_key]" value=""
							autocomplete="new-password" spellcheck="false"
							placeholder="<?php echo esc_attr( $nzd_has_key ? $nzd_masked : 'nzd_live_...' ); ?>" />
						<p class="description">
							<?php if ( $nzd_has_key ) : ?>
								<?php echo esc_html__( 'A key is stored (masked above). Leave this field blank to keep it.', 'nzdworld-link-generator' ); ?>
							<?php else : ?>
								<?php echo esc_html__( 'Generate a key in NZDWorld -> Control Center -> Link Generator, then paste it here.', 'nzdworld-link-generator' ); ?>
							<?php endif; ?>
						</p>
						<?php if ( $nzd_has_key ) : ?>
							<label class="description">
								<input type="checkbox" name="nzd_lg[clear_api_key]" value="1" />
								<?php echo esc_html__( 'Remove the stored key', 'nzdworld-link-generator' ); ?>
							</label>
						<?php endif; ?>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="nzd-lg-blog-url"><?php echo esc_html__( 'Blog page link', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<input type="url" class="regular-text" id="nzd-lg-blog-url" name="nzd_lg[blog_page_url]"
							value="<?php echo esc_attr( $settings['blog_page_url'] ); ?>" placeholder="<?php echo esc_attr( home_url( '/jobs/' ) ); ?>" />
						<p class="description">
							<?php echo esc_html__( 'The page that holds your job articles on this site (same host). Used as the popup page when random article routing is off, and shown to you for verification.', 'nzdworld-link-generator' ); ?>
						</p>
					</td>
				</tr>
				<tr>
					<th scope="row"><?php echo esc_html__( 'Test connection', 'nzdworld-link-generator' ); ?></th>
					<td>
						<p class="description">
							<?php echo esc_html__( 'Saving with "Save & Test Connection" registers this site with NZDWorld and stores the entry URL. Do this once after pasting the API key.', 'nzdworld-link-generator' ); ?>
						</p>
					</td>
				</tr>
			</table>
		</div>

		<div class="card" style="max-width:860px;padding:6px 18px 14px;margin-top:16px">
			<h2 style="margin-bottom:6px"><?php echo esc_html__( 'Generator', 'nzdworld-link-generator' ); ?></h2>
			<table class="form-table" role="presentation">
				<tr>
					<th scope="row"><label for="nzd-lg-countdown"><?php echo esc_html__( 'Countdown duration', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<input type="number" class="small-text" id="nzd-lg-countdown" name="nzd_lg[countdown_seconds]"
							value="<?php echo esc_attr( (string) $settings['countdown_seconds'] ); ?>" min="5" max="120" step="1" />
						<?php echo esc_html__( 'seconds (5-120). NZDWorld enforces the same deadline on its side.', 'nzdworld-link-generator' ); ?>
					</td>
				</tr>
				<tr>
					<th scope="row"><?php echo esc_html__( 'Article routing', 'nzdworld-link-generator' ); ?></th>
					<td>
						<label style="display:block;margin-bottom:6px">
							<input type="checkbox" name="nzd_lg[random_article]" value="1" <?php checked( 1, (int) $settings['random_article'] ); ?> />
							<?php echo esc_html__( 'Send visitors to a random published job article', 'nzdworld-link-generator' ); ?>
						</label>
						<p class="description">
							<?php echo esc_html__( 'When off, the popup opens on the blog page link above instead of an article.', 'nzdworld-link-generator' ); ?>
						</p>
					</td>
				</tr>
				<tr>
					<th scope="row"><?php echo esc_html__( 'Eligible articles', 'nzdworld-link-generator' ); ?></th>
					<td>
						<label style="display:block;margin-bottom:8px">
							<input type="checkbox" name="nzd_lg[use_all_posts]" value="1" <?php checked( 1, (int) $settings['use_all_posts'] ); ?> />
							<?php echo esc_html__( 'Use all published posts (ignore the category list below)', 'nzdworld-link-generator' ); ?>
						</label>
						<label for="nzd-lg-categories" style="display:block;margin-bottom:4px">
							<?php echo esc_html__( 'Allowed job article categories:', 'nzdworld-link-generator' ); ?>
						</label>
						<select id="nzd-lg-categories" name="nzd_lg[allowed_categories][]" multiple size="6" style="min-width:280px">
							<?php
							$nzd_selected = array_map( 'absint', (array) $settings['allowed_categories'] );
							foreach ( (array) $categories as $nzd_category ) :
								?>
								<option value="<?php echo esc_attr( (string) $nzd_category->term_id ); ?>"
									<?php echo in_array( (int) $nzd_category->term_id, $nzd_selected, true ) ? 'selected="selected"' : ''; ?>>
									<?php echo esc_html( $nzd_category->name ); ?>
									(<?php echo esc_html( (string) $nzd_category->count ); ?>)
								</option>
							<?php endforeach; ?>
						</select>
						<p class="description">
							<?php echo esc_html__( 'Only published posts are ever chosen - never drafts, private, trashed or password-protected content.', 'nzdworld-link-generator' ); ?>
						</p>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="nzd-lg-ttl"><?php echo esc_html__( 'Session expiration', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<input type="number" class="small-text" id="nzd-lg-ttl" name="nzd_lg[session_ttl]"
							value="<?php echo esc_attr( (string) $settings['session_ttl'] ); ?>" min="120" max="<?php echo esc_attr( (string) $nzd_max_ttl ); ?>" step="10" />
						<?php echo esc_html__( 'seconds. Capped by NZDWorld at', 'nzdworld-link-generator' ); ?>
						<code><?php echo esc_html( (string) $nzd_max_ttl ); ?></code>
						<?php echo esc_html__( 'seconds.', 'nzdworld-link-generator' ); ?>
					</td>
				</tr>
			</table>
		</div>

		<div class="card" style="max-width:860px;padding:6px 18px 14px;margin-top:16px">
			<h2 style="margin-bottom:6px"><?php echo esc_html__( 'Popup appearance', 'nzdworld-link-generator' ); ?></h2>
			<table class="form-table" role="presentation">
				<tr>
					<th scope="row"><label for="nzd-lg-heading"><?php echo esc_html__( 'Heading', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<input type="text" class="regular-text" id="nzd-lg-heading" name="nzd_lg[popup_heading]"
							value="<?php echo esc_attr( $settings['popup_heading'] ); ?>" maxlength="120" />
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="nzd-lg-description"><?php echo esc_html__( 'Description', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<textarea class="large-text" rows="3" id="nzd-lg-description" name="nzd_lg[popup_description]"
							maxlength="400"><?php echo esc_textarea( $settings['popup_description'] ); ?></textarea>
						<p class="description"><?php echo esc_html__( 'Plain text. Keep it short and reassuring.', 'nzdworld-link-generator' ); ?></p>
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="nzd-lg-button"><?php echo esc_html__( 'Button text', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<input type="text" class="regular-text" id="nzd-lg-button" name="nzd_lg[popup_button_text]"
							value="<?php echo esc_attr( $settings['popup_button_text'] ); ?>" maxlength="40" />
					</td>
				</tr>
				<tr>
					<th scope="row"><label for="nzd-lg-logo"><?php echo esc_html__( 'Logo URL (optional)', 'nzdworld-link-generator' ); ?></label></th>
					<td>
						<input type="url" class="regular-text" id="nzd-lg-logo" name="nzd_lg[popup_logo_url]"
							value="<?php echo esc_attr( $settings['popup_logo_url'] ); ?>" placeholder="https://this-site.com/wp-content/uploads/logo.png" />
						<p class="description"><?php echo esc_html__( 'Absolute HTTPS image URL. Leave empty for the built-in download icon.', 'nzdworld-link-generator' ); ?></p>
					</td>
				</tr>
			</table>
		</div>

		<p class="submit" style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
			<button type="submit" class="button button-primary"><?php echo esc_html__( 'Save settings', 'nzdworld-link-generator' ); ?></button>
			<button type="submit" class="button" name="nzd_lg_test" value="1"><?php echo esc_html__( 'Save & Test Connection', 'nzdworld-link-generator' ); ?></button>
		</p>
	</form>

	<div class="card" style="max-width:860px;padding:6px 18px 14px;margin-top:16px">
		<h2 style="margin-bottom:6px"><?php echo esc_html__( 'Statistics', 'nzdworld-link-generator' ); ?></h2>
		<table class="widefat striped" style="max-width:620px">
			<tbody>
				<tr>
					<td><?php echo esc_html__( 'Received requests', 'nzdworld-link-generator' ); ?></td>
					<td><strong><?php echo esc_html( (string) (int) $stats['received_requests'] ); ?></strong></td>
				</tr>
				<tr>
					<td><?php echo esc_html__( 'Successful generations', 'nzdworld-link-generator' ); ?></td>
					<td><strong><?php echo esc_html( (string) (int) $stats['successful_generations'] ); ?></strong></td>
				</tr>
				<tr>
					<td><?php echo esc_html__( 'Expired sessions', 'nzdworld-link-generator' ); ?></td>
					<td><strong><?php echo esc_html( (string) (int) $stats['expired_sessions'] ); ?></strong></td>
				</tr>
				<tr>
					<td><?php echo esc_html__( 'Failed requests', 'nzdworld-link-generator' ); ?></td>
					<td><strong><?php echo esc_html( (string) (int) $stats['failed_requests'] ); ?></strong></td>
				</tr>
				<tr>
					<td><?php echo esc_html__( 'Open sessions (ready / redeemed / expired / failed)', 'nzdworld-link-generator' ); ?></td>
					<td>
						<?php
						echo esc_html(
							(int) $session_counts['ready'] . ' / ' .
							(int) $session_counts['redeemed'] . ' / ' .
							(int) $session_counts['expired'] . ' / ' .
							(int) $session_counts['failed']
						);
						?>
					</td>
				</tr>
				<tr>
					<td><?php echo esc_html__( 'Last request', 'nzdworld-link-generator' ); ?></td>
					<td>
						<?php
						echo '' !== (string) $stats['last_request_at']
							? '<code>' . esc_html( $stats['last_request_at'] ) . ' UTC</code>'
							: '-';
						?>
					</td>
				</tr>
			</tbody>
		</table>
		<p class="description">
			<?php echo esc_html__( 'Sessions are cleaned up hourly; abandoned sessions are expired automatically.', 'nzdworld-link-generator' ); ?>
		</p>
	</div>

	<div class="card" style="max-width:860px;padding:6px 18px 14px;margin-top:16px">
		<h2 style="margin-bottom:6px"><?php echo esc_html__( 'How it works', 'nzdworld-link-generator' ); ?></h2>
		<ol style="margin:0 0 8px 18px;line-height:1.7">
			<li><?php echo esc_html__( 'A visitor clicks a download button on NZDWorld; NZDWorld creates a one-time token and redirects here.', 'nzdworld-link-generator' ); ?></li>
			<li><?php echo esc_html__( 'This plugin verifies the token with NZDWorld over HTTPS, picks a published job article and redirects to it with an opaque identifier.', 'nzdworld-link-generator' ); ?></li>
			<li><?php echo esc_html__( 'The countdown popup opens. Refreshing keeps the same remaining time because the deadline is stored on the server.', 'nzdworld-link-generator' ); ?></li>
			<li><?php echo esc_html__( 'When the visitor clicks the button after the countdown, this site asks NZDWorld to redeem the session; NZDWorld answers with the original download link.', 'nzdworld-link-generator' ); ?></li>
		</ol>
		<p class="description">
			<?php echo esc_html__( 'Article URL pattern:', 'nzdworld-link-generator' ); ?>
			<code><?php echo esc_html( trailingslashit( home_url( '/' ) ) . 'your-job-article/' ); ?>?nzd_gen=&lt;opaque-id&gt;</code>
		</p>
		<p class="description">
			<?php echo esc_html__( 'The API key is stored in the WordPress options table and is never sent to visitors.', 'nzdworld-link-generator' ); ?>
			<a href="<?php echo esc_url( $nzd_page_url ); ?>"><?php echo esc_html__( 'Reload this screen', 'nzdworld-link-generator' ); ?></a>
		</p>
	</div>
</div>

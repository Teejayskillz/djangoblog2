<?php
/**
 * Generator popup markup.
 *
 * Rendered in wp_footer only when a live session matches the current article and
 * visitor. All user-configurable text is escaped here; the only value that
 * reaches JavaScript is the opaque public session id plus a WordPress nonce.
 *
 * @package NZDWorld_Link_Generator
 *
 * @var array $view Heading/description/button/logo/remaining/nzd_url.
 */

defined( 'ABSPATH' ) || exit;

if ( ! isset( $view ) || ! is_array( $view ) ) {
	return;
}

$nzd_heading     = isset( $view['heading'] ) ? (string) $view['heading'] : '';
$nzd_description = isset( $view['description'] ) ? (string) $view['description'] : '';
$nzd_button      = isset( $view['button'] ) ? (string) $view['button'] : '';
$nzd_logo        = isset( $view['logo'] ) ? (string) $view['logo'] : '';
$nzd_remaining   = isset( $view['remaining'] ) ? max( 0, (int) $view['remaining'] ) : 0;
$nzd_return_url  = isset( $view['nzd_url'] ) ? (string) $view['nzd_url'] : home_url( '/' );
?>
<div id="nzd-lg-overlay" class="nzd-lg-overlay" hidden>
	<div id="nzd-lg-modal" class="nzd-lg-modal" role="dialog" aria-modal="true"
		aria-labelledby="nzd-lg-title" aria-describedby="nzd-lg-desc" tabindex="-1">

		<button type="button" class="nzd-lg-close" id="nzd-lg-close"
			aria-label="<?php echo esc_attr__( 'Close', 'nzdworld-link-generator' ); ?>">
			<span aria-hidden="true">&times;</span>
		</button>

		<div class="nzd-lg-head">
			<?php if ( '' !== $nzd_logo ) : ?>
				<img class="nzd-lg-logo" src="<?php echo esc_url( $nzd_logo ); ?>" alt="" width="56" height="56" loading="lazy" />
			<?php else : ?>
				<span class="nzd-lg-logo nzd-lg-logo-fallback" aria-hidden="true">
					<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.8"
						stroke-linecap="round" stroke-linejoin="round">
						<path d="M12 3v12" />
						<path d="m7 10 5 5 5-5" />
						<path d="M5 21h14" />
					</svg>
				</span>
			<?php endif; ?>
			<h2 class="nzd-lg-title" id="nzd-lg-title"><?php echo esc_html( $nzd_heading ); ?></h2>
			<p class="nzd-lg-desc" id="nzd-lg-desc"><?php echo nl2br( esc_html( $nzd_description ) ); ?></p>
		</div>

		<div class="nzd-lg-waiting" id="nzd-lg-waiting">
			<div class="nzd-lg-ring-wrap">
				<svg class="nzd-lg-ring" viewBox="0 0 120 120" aria-hidden="true" focusable="false">
					<circle class="nzd-lg-ring-track" cx="60" cy="60" r="52"></circle>
					<circle class="nzd-lg-ring-value" id="nzd-lg-ring" cx="60" cy="60" r="52"></circle>
				</svg>
				<span class="nzd-lg-seconds" id="nzd-lg-seconds"><?php echo esc_html( (string) $nzd_remaining ); ?></span>
			</div>

			<div class="nzd-lg-progress" id="nzd-lg-progress" role="progressbar"
				aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"
				aria-label="<?php echo esc_attr__( 'Download generation progress', 'nzdworld-link-generator' ); ?>">
				<span class="nzd-lg-progress-bar" id="nzd-lg-bar"></span>
			</div>

			<p class="nzd-lg-status" id="nzd-lg-status" aria-live="polite">
				<?php echo esc_html__( 'Please wait while we prepare your link.', 'nzdworld-link-generator' ); ?>
				<span class="nzd-lg-status-seconds" id="nzd-lg-status-seconds"></span>
			</p>
		</div>

		<div class="nzd-lg-ready" id="nzd-lg-ready" hidden>
			<div class="nzd-lg-check" aria-hidden="true">
				<svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.4"
					stroke-linecap="round" stroke-linejoin="round">
					<path d="m5 13 4 4L19 7" />
				</svg>
			</div>
			<p class="nzd-lg-ready-text">
				<?php echo esc_html__( 'Your link is ready. Continue to your download.', 'nzdworld-link-generator' ); ?>
			</p>
			<button type="button" class="nzd-lg-button" id="nzd-lg-proceed">
				<?php echo esc_html( $nzd_button ); ?>
			</button>
		</div>

		<div class="nzd-lg-error" id="nzd-lg-error" hidden>
			<p class="nzd-lg-error-text" id="nzd-lg-error-text" aria-live="assertive"></p>
			<div class="nzd-lg-error-actions">
				<button type="button" class="nzd-lg-button" id="nzd-lg-retry">
					<?php echo esc_html__( 'Try again', 'nzdworld-link-generator' ); ?>
				</button>
				<a class="nzd-lg-link" id="nzd-lg-back" href="<?php echo esc_url( $nzd_return_url ); ?>">
					<?php echo esc_html__( 'Return to NZDWorld', 'nzdworld-link-generator' ); ?>
				</a>
			</div>
		</div>

		<noscript>
			<p class="nzd-lg-noscript">
				<?php echo esc_html__( 'JavaScript is required to finish generating your download link.', 'nzdworld-link-generator' ); ?>
			</p>
		</noscript>
	</div>
</div>

<button type="button" id="nzd-lg-reopen" class="nzd-lg-reopen" hidden>
	<?php echo esc_html__( 'Resume download', 'nzdworld-link-generator' ); ?>
</button>

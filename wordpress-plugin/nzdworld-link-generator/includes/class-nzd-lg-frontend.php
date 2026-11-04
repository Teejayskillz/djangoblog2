<?php
/**
 * Frontend popup, assets and the AJAX redemption endpoint.
 *
 * The popup is only prepared when the current request carries a valid `nzd_gen`
 * identifier that matches a live server-side session, the current article and
 * this browser. A direct visit to the same article shows nothing.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * Frontend integration.
 */
final class NZD_LG_Frontend {

	/**
	 * The validated session for the current request (null when none).
	 *
	 * @var array|null
	 */
	private static $session = null;

	/**
	 * Register hooks.
	 *
	 * @return void
	 */
	public static function init() {
		add_action( 'template_redirect', array( __CLASS__, 'detect_session' ), 5 );
		add_action( 'wp_enqueue_scripts', array( __CLASS__, 'enqueue_assets' ) );
		add_action( 'wp_footer', array( __CLASS__, 'render_modal' ), 99 );
		add_action( 'wp_ajax_nzdworld_redeem', array( __CLASS__, 'ajax_redeem' ) );
		add_action( 'wp_ajax_nopriv_nzdworld_redeem', array( __CLASS__, 'ajax_redeem' ) );
	}

	/**
	 * Find and validate the session for this page view.
	 *
	 * @return void
	 */
	public static function detect_session() {
		if ( is_admin() || wp_doing_ajax() || wp_doing_cron() ) {
			return;
		}

		if ( ! isset( $_GET['nzd_gen'] ) || ! is_string( $_GET['nzd_gen'] ) ) {
			return;
		}
		$public_id = sanitize_text_field( wp_unslash( $_GET['nzd_gen'] ) );
		if ( ! preg_match( '/^[a-f0-9]{32}$/', $public_id ) ) {
			return;
		}

		$session = NZD_LG_Sessions::get( $public_id );
		if ( ! $session ) {
			return;
		}

		if ( NZD_LG_Sessions::STATUS_READY !== $session['status'] ) {
			return;
		}

		if ( (int) $session['expires_at'] <= time() ) {
			if ( NZD_LG_Sessions::mark_expired( $session['id'] ) ) {
				NZD_LG_Options::bump_stat( 'expired_sessions' );
			}
			return;
		}

		if ( ! NZD_LG_Entry::session_matches_visitor( $session ) ) {
			return;
		}

		if ( ! self::is_session_page( $session ) ) {
			return;
		}

		self::$session = $session;
		NZD_LG_Sessions::touch( $session['id'] );

		if ( ! defined( 'DONOTCACHEPAGE' ) ) {
			define( 'DONOTCACHEPAGE', true );
		}
		nocache_headers();
	}

	/**
	 * Whether the current request is the page the session was created for.
	 *
	 * @param array $session Session row.
	 * @return bool
	 */
	public static function is_session_page( $session ) {
		$article_id = (int) $session['article_id'];

		if ( $article_id > 0 ) {
			return is_singular( 'post' ) && (int) get_queried_object_id() === $article_id;
		}

		// "Blog page" mode: match the configured page path exactly.
		$expected = (string) wp_parse_url( (string) $session['article_url'], PHP_URL_PATH );
		$current  = isset( $_SERVER['REQUEST_URI'] ) ? wp_unslash( $_SERVER['REQUEST_URI'] ) : '/';
		$current  = (string) wp_parse_url( (string) $current, PHP_URL_PATH );

		return untrailingslashit( $expected ) === untrailingslashit( $current );
	}

	/**
	 * Load CSS/JS and hand the (public, non-secret) config to the script.
	 *
	 * @return void
	 */
	public static function enqueue_assets() {
		if ( null === self::$session ) {
			return;
		}

		$settings = NZD_LG_Options::get();

		wp_enqueue_style(
			'nzd-lg-generator',
			NZD_LG_URL . 'assets/css/generator.css',
			array(),
			NZD_LG_VERSION
		);

		wp_enqueue_script(
			'nzd-lg-generator',
			NZD_LG_URL . 'assets/js/generator.js',
			array(),
			NZD_LG_VERSION,
			true
		);

		$remaining = max( 0, (int) self::$session['not_before'] - time() );

		wp_localize_script(
			'nzd-lg-generator',
			'NzdLgConfig',
			array(
				'ajaxUrl'   => admin_url( 'admin-ajax.php' ),
				'action'    => 'nzdworld_redeem',
				'nonce'     => wp_create_nonce( 'nzd_lg_redeem' ),
				'session'   => self::$session['public_id'],
				'remaining' => $remaining,
				'duration'  => max( 1, (int) $settings['countdown_seconds'] ),
				'strings'   => array(
					'waiting'    => __( 'Please wait while we prepare your link.', 'nzdworld-link-generator' ),
					'readyTitle' => __( 'Your download link is ready', 'nzdworld-link-generator' ),
					'readyText'  => __( 'Your link has been prepared. Click the button below to continue.', 'nzdworld-link-generator' ),
					'errorTitle' => __( 'Something went wrong', 'nzdworld-link-generator' ),
					'network'    => __( 'We could not reach NZDWorld. Please check your connection and try again.', 'nzdworld-link-generator' ),
					'retry'      => __( 'Try again', 'nzdworld-link-generator' ),
					'seconds'    => __( 'seconds', 'nzdworld-link-generator' ),
					'reopen'     => __( 'Resume download', 'nzdworld-link-generator' ),
				),
			)
		);
	}

	/**
	 * Render the modal markup in the footer.
	 *
	 * @return void
	 */
	public static function render_modal() {
		if ( null === self::$session ) {
			return;
		}

		$settings   = NZD_LG_Options::get();
		$remaining  = max( 0, (int) self::$session['not_before'] - time() );
		$nzd_url    = '' !== $settings['nzd_site_url'] ? $settings['nzd_site_url'] : home_url( '/' );

		$view = array(
			'heading'     => '' !== $settings['popup_heading']
				? $settings['popup_heading']
				: __( 'Generating your download link', 'nzdworld-link-generator' ),
			'description' => '' !== $settings['popup_description']
				? $settings['popup_description']
				: __( 'Please wait while we prepare your link.', 'nzdworld-link-generator' ),
			'button'      => '' !== $settings['popup_button_text']
				? $settings['popup_button_text']
				: __( 'Click to Proceed', 'nzdworld-link-generator' ),
			'logo'        => (string) $settings['popup_logo_url'],
			'remaining'   => $remaining,
			'nzd_url'     => $nzd_url,
		);

		$template = NZD_LG_DIR . 'templates/modal.php';
		if ( is_readable( $template ) ) {
			include $template;
		}
	}

	/**
	 * AJAX endpoint: redeem the session and release the destination URL.
	 *
	 * @return void
	 */
	public static function ajax_redeem() {
		nocache_headers();

		if ( ! check_ajax_referer( 'nzd_lg_redeem', 'nonce', false ) ) {
			wp_send_json_error(
				array(
					'code'      => 'bad_nonce',
					'message'   => 'Your page session expired. Please reload the page and try again.',
					'retryable' => true,
				),
				403
			);
		}

		if ( ! NZD_LG_Options::is_enabled() ) {
			wp_send_json_error(
				array(
					'code'      => 'integration_disabled',
					'message'   => NZD_LG_Api_Client::map_error( 'integration_disabled' ),
					'retryable' => false,
				),
				403
			);
		}

		if ( ! NZD_LG_Options::is_configured() ) {
			wp_send_json_error(
				array(
					'code'      => 'not_configured',
					'message'   => NZD_LG_Api_Client::map_error( 'not_configured' ),
					'retryable' => false,
				),
				503
			);
		}

		if ( ! self::redeem_rate_limit_ok() ) {
			wp_send_json_error(
				array(
					'code'      => 'rate_limited',
					'message'   => NZD_LG_Api_Client::map_error( 'rate_limited' ),
					'retryable' => true,
				),
				429
			);
		}

		$public_id = ( isset( $_POST['session'] ) && is_string( $_POST['session'] ) )
			? sanitize_text_field( wp_unslash( $_POST['session'] ) )
			: '';
		if ( ! preg_match( '/^[a-f0-9]{32}$/', $public_id ) ) {
			wp_send_json_error(
				array(
					'code'      => 'invalid_session',
					'message'   => 'This download session is not valid. Please start again from NZDWorld.',
					'retryable' => false,
				),
				400
			);
		}

		$session = NZD_LG_Sessions::get( $public_id );
		if ( ! $session ) {
			wp_send_json_error(
				array(
					'code'      => 'invalid_session',
					'message'   => 'This download session is not valid. Please start again from NZDWorld.',
					'retryable' => false,
				),
				404
			);
		}

		if ( ! NZD_LG_Entry::session_matches_visitor( $session ) ) {
			wp_send_json_error(
				array(
					'code'      => 'forbidden',
					'message'   => 'This download session belongs to another visitor. Please start again from NZDWorld.',
					'retryable' => false,
				),
				403
			);
		}

		if ( NZD_LG_Sessions::STATUS_REDEEMED === $session['status'] ) {
			wp_send_json_error( self::error_payload( 'session_redeemed' ), 409 );
		}

		if ( NZD_LG_Sessions::STATUS_READY !== $session['status'] || (int) $session['expires_at'] <= time() ) {
			if ( NZD_LG_Sessions::mark_expired( $session['id'] ) ) {
				NZD_LG_Options::bump_stat( 'expired_sessions' );
			}
			NZD_LG_Plugin::log( 'redeem refused: session_expired' );
			wp_send_json_error( self::error_payload( 'session_expired' ), 410 );
		}

		// Server-side countdown enforcement. The browser countdown is cosmetic:
		// even if the JavaScript is edited, this check still refuses early calls.
		$now = time();
		if ( $now < (int) $session['not_before'] ) {
			wp_send_json_error(
				array(
					'code'      => 'too_early',
					'message'   => 'Please wait for the countdown to finish.',
					'retryable' => true,
					'remaining' => max( 1, (int) $session['not_before'] - $now ),
				),
				409
			);
		}

		$token = NZD_LG_Crypto::decrypt( $session['token_cipher'] );
		if ( '' === $token ) {
			NZD_LG_Sessions::mark_status( $session['id'], NZD_LG_Sessions::STATUS_FAILED );
			wp_send_json_error(
				array(
					'code'      => 'invalid_session',
					'message'   => 'This download session could not be read. Please start again from NZDWorld.',
					'retryable' => false,
				),
				400
			);
		}

		$client = NZD_LG_Api_Client::from_settings();
		$result = $client->redeem( $token );

		if ( empty( $result['ok'] ) ) {
			$code = isset( $result['code'] ) ? (string) $result['code'] : 'server_error';

			if ( 'session_expired' === $code ) {
				if ( NZD_LG_Sessions::mark_expired( $session['id'] ) ) {
					NZD_LG_Options::bump_stat( 'expired_sessions' );
				}
			} elseif ( 'session_redeemed' === $code ) {
				NZD_LG_Sessions::mark_status( $session['id'], NZD_LG_Sessions::STATUS_REDEEMED );
			} elseif ( 'too_early' === $code ) {
				$retry = isset( $result['retry_after'] ) ? (int) $result['retry_after'] : 5;
				wp_send_json_error(
					array(
						'code'      => 'too_early',
						'message'   => 'Please wait for the countdown to finish.',
						'retryable' => true,
						'remaining' => max( 1, $retry ),
					),
					409
				);
			}

			$status = in_array( $code, array( 'network_error', 'server_error' ), true ) ? 502 : 400;
			wp_send_json_error(
				array(
					'code'      => $code,
					'message'   => NZD_LG_Entry::visitor_message( $result ),
					'retryable' => in_array( $code, array( 'network_error', 'server_error', 'rate_limited', 'stale_request', 'replayed_request' ), true ),
				),
				$status
			);
		}

		$destination = NZD_LG_Api_Client::destination_from( $result );
		if ( '' === $destination ) {
			NZD_LG_Sessions::mark_status( $session['id'], NZD_LG_Sessions::STATUS_FAILED );
			wp_send_json_error(
				array(
					'code'      => 'invalid_response',
					'message'   => 'NZDWorld did not return a valid download link. Please try again.',
					'retryable' => true,
				),
				502
			);
		}

		// NZDWorld has already claimed the session atomically; this local update
		// only stops the same browser from showing the popup again.
		NZD_LG_Sessions::mark_redeemed( $session['id'] );
		NZD_LG_Options::bump_stat( 'successful_generations' );

		wp_send_json_success(
			array(
				'redirect' => $destination,
				'message'  => 'Your download is starting.',
			)
		);
	}

	/**
	 * Standard error payload.
	 *
	 * @param string $code Error code.
	 * @return array
	 */
	private static function error_payload( $code ) {
		return array(
			'code'      => $code,
			'message'   => NZD_LG_Api_Client::map_error( $code ),
			'retryable' => false,
		);
	}

	/**
	 * Rate limit redemption attempts per visitor IP.
	 *
	 * @return bool
	 */
	private static function redeem_rate_limit_ok() {
		$limit = (int) apply_filters( 'nzd_lg_redeem_rate_limit', 15 );
		$key   = 'nzd_lg_redeem_' . md5( NZD_LG_Entry::client_ip() );
		$hits  = (int) get_transient( $key );

		if ( $hits >= $limit ) {
			return false;
		}
		set_transient( $key, $hits + 1, 10 * MINUTE_IN_SECONDS );
		return true;
	}
}

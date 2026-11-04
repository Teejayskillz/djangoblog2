<?php
/**
 * Entry route for incoming NZDWorld generation requests.
 *
 * URL shape:  https://this-site.example/nzdworld-link/{token}/
 * Fallback:   https://this-site.example/?nzdworld_token={token}
 *
 * The token is validated against NZDWorld (server to server) before anything is
 * shown. Only then is a random published job article picked, a private
 * WordPress session created, and the visitor redirected in the same tab to that
 * article with an opaque `nzd_gen` identifier in the URL.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * Handles the incoming handoff.
 */
final class NZD_LG_Entry {

	/**
	 * Opaque NZDWorld token format (urlsafe base64, 32 random bytes).
	 */
	const TOKEN_PATTERN = '/^[A-Za-z0-9_-]{32,96}$/';

	/**
	 * Visitor cookie name.
	 */
	const VISITOR_COOKIE = 'nzd_lg_v';

	/**
	 * Register hooks.
	 *
	 * @return void
	 */
	public static function init() {
		add_action( 'init', array( __CLASS__, 'add_rewrite_rule' ) );
		add_filter( 'query_vars', array( __CLASS__, 'register_query_var' ) );
		add_action( 'template_redirect', array( __CLASS__, 'maybe_handle' ), 0 );
	}

	/**
	 * Pretty entry route (requires permalinks; a path fallback exists too).
	 *
	 * @return void
	 */
	public static function add_rewrite_rule() {
		add_rewrite_rule(
			'^' . preg_quote( NZD_LG_ENTRY_SLUG, '/' ) . '/([A-Za-z0-9_-]{32,96})/?$',
			'index.php?nzdworld_token=$matches[1]',
			'top'
		);
	}

	/**
	 * Make the entry token a public query var.
	 *
	 * @param array $vars Query vars.
	 * @return array
	 */
	public static function register_query_var( $vars ) {
		$vars[] = 'nzdworld_token';
		return $vars;
	}

	/**
	 * Detect an entry request and handle it.
	 *
	 * @return void
	 */
	public static function maybe_handle() {
		if ( is_admin() || wp_doing_ajax() || wp_doing_cron() ) {
			return;
		}
		if ( defined( 'REST_REQUEST' ) && REST_REQUEST ) {
			return;
		}

		$token = self::detect_token();
		if ( '' === $token ) {
			return;
		}

		self::handle( $token );
		exit;
	}

	/**
	 * Extract the token from the query var, the query string or the path.
	 *
	 * @return string
	 */
	public static function detect_token() {
		$token = get_query_var( 'nzdworld_token' );

		if ( ! is_string( $token ) || '' === $token ) {
			if ( isset( $_GET['nzdworld_token'] ) && is_string( $_GET['nzdworld_token'] ) ) {
				$token = sanitize_text_field( wp_unslash( $_GET['nzdworld_token'] ) );
			}
		}

		if ( ! is_string( $token ) || '' === $token ) {
			$request_uri = isset( $_SERVER['REQUEST_URI'] ) ? wp_unslash( $_SERVER['REQUEST_URI'] ) : '';
			$path        = (string) wp_parse_url( (string) $request_uri, PHP_URL_PATH );
			$needle      = '/' . NZD_LG_ENTRY_SLUG . '/';
			$position    = strpos( $path, $needle );
			if ( false !== $position ) {
				$rest     = trim( substr( $path, $position + strlen( $needle ) ), '/' );
				$segments = explode( '/', $rest );
				$token    = $segments[0];
			}
		}

		return is_string( $token ) ? trim( $token ) : '';
	}

	/**
	 * Validate the token, choose an article and redirect.
	 *
	 * @param string $token Raw token from the URL.
	 * @return void
	 */
	public static function handle( $token ) {
		$settings = NZD_LG_Options::get();

		if ( empty( $settings['enabled'] ) ) {
			self::fail( 'disabled', 403, 'The download generator is currently switched off on this site.' );
			return;
		}
		if ( '' === $settings['nzd_site_url'] || '' === $settings['api_key'] ) {
			self::fail( 'not_configured', 503, 'The download generator is not configured yet. Please try again later.' );
			return;
		}
		if ( ! NZD_LG_Api_Client::is_secure_url( home_url( '/' ) ) ) {
			self::fail( 'insecure_site', 503, 'This site must be served over HTTPS for secure downloads.' );
			return;
		}
		if ( ! NZD_LG_Crypto::available() ) {
			self::fail( 'crypto_unavailable', 503, 'The download generator cannot start because PHP OpenSSL is unavailable.' );
			return;
		}
		if ( ! preg_match( self::TOKEN_PATTERN, $token ) ) {
			self::fail( 'invalid_token', 404, 'This download link is not valid.' );
			return;
		}
		if ( ! self::rate_limit_ok() ) {
			self::fail( 'rate_limited', 429, 'Too many download requests. Please wait a moment and try again.' );
			return;
		}

		NZD_LG_Options::bump_stat( 'received_requests' );

		$client = NZD_LG_Api_Client::from_settings();
		$result = $client->validate( $token, (int) $settings['countdown_seconds'] );

		if ( empty( $result['ok'] ) ) {
			$code = isset( $result['code'] ) ? (string) $result['code'] : 'invalid_response';
			if ( 'session_expired' === $code ) {
				NZD_LG_Options::bump_stat( 'expired_sessions' );
			}
			self::fail( $code, self::status_for( $code ), self::visitor_message( $result ) );
			return;
		}

		$payload = isset( $result['data']['session'] ) && is_array( $result['data']['session'] )
			? $result['data']['session']
			: array();

		$not_before = self::parse_time( isset( $payload['not_before'] ) ? $payload['not_before'] : '' );
		$expires_at = self::parse_time( isset( $payload['expires_at'] ) ? $payload['expires_at'] : '' );
		$now        = time();

		if ( $expires_at <= $now ) {
			NZD_LG_Options::bump_stat( 'expired_sessions' );
			self::fail( 'session_expired', 410, 'This download link has expired. Please start again from NZDWorld.' );
			return;
		}
		if ( $not_before <= 0 ) {
			self::fail( 'invalid_response', 503, 'NZDWorld returned an unusable session. Please try again.' );
			return;
		}

		$visitor      = self::visitor_id( true );
		$visitor_hash = NZD_LG_Crypto::hash( $visitor );

		// The same browser reloading the entry URL must land on the same article
		// without creating another session (and without restarting the countdown).
		$existing = NZD_LG_Sessions::get_by_token_hash( hash( 'sha256', $token ) );
		if ( $existing && NZD_LG_Sessions::is_live( $existing ) && hash_equals( (string) $existing['visitor_hash'], $visitor_hash ) ) {
			self::redirect_to( $existing['article_url'], $existing['public_id'] );
			return;
		}

		$article = self::select_article( $settings );
		if ( is_wp_error( $article ) ) {
			NZD_LG_Options::bump_stat( 'failed_requests' );
			self::fail( 'no_articles', 503, $article->get_error_message() );
			return;
		}

		$cipher = NZD_LG_Crypto::encrypt( $token );
		if ( '' === $cipher ) {
			self::fail( 'crypto_unavailable', 503, 'The download generator could not protect the session.' );
			return;
		}

		// Never outlive what NZDWorld allowed, and always leave room to redeem.
		$settings_expires = $now + max( 60, (int) $settings['session_ttl'] );
		if ( $settings_expires < $not_before + 30 ) {
			$settings_expires = $not_before + 30;
		}
		$session_expires = min( $expires_at, $settings_expires );

		$public_id = NZD_LG_Sessions::create(
			array(
				'public_id'    => NZD_LG_Sessions::new_public_id(),
				'token_hash'   => hash( 'sha256', $token ),
				'token_cipher' => $cipher,
				'article_id'   => (int) $article['id'],
				'article_url'  => (string) $article['url'],
				'visitor_hash' => $visitor_hash,
				'ip_hash'      => NZD_LG_Crypto::hash( self::client_ip() ),
				'created_at'   => $now,
				'not_before'   => $not_before,
				'expires_at'   => $session_expires,
			)
		);

		if ( '' === $public_id ) {
			NZD_LG_Options::bump_stat( 'failed_requests' );
			self::fail( 'storage_error', 500, 'The download generator could not create a session. Please try again.' );
			return;
		}

		self::set_visitor_cookie( $visitor );
		self::redirect_to( $article['url'], $public_id );
	}

	/**
	 * Pick the article that will display the popup.
	 *
	 * @param array $settings Plugin settings.
	 * @return array|WP_Error {id, url}
	 */
	public static function select_article( $settings ) {
		if ( empty( $settings['random_article'] ) ) {
			$url = NZD_LG_Options::blog_url();
			if ( ! self::is_same_site_url( $url ) ) {
				return new WP_Error( 'bad_blog_url', 'The configured blog page URL is not on this site.' );
			}
			return array(
				'id'  => 0,
				'url' => $url,
			);
		}

		$args = array(
			'post_type'           => 'post',
			'post_status'         => 'publish',
			'posts_per_page'      => 1,
			'orderby'             => 'rand',
			'no_found_rows'       => true,
			'ignore_sticky_posts' => true,
			'fields'              => 'ids',
			'has_password'        => false,
		);

		if ( empty( $settings['use_all_posts'] ) ) {
			$categories = array_values( array_filter( array_map( 'absint', (array) $settings['allowed_categories'] ) ) );
			if ( empty( $categories ) ) {
				return new WP_Error(
					'no_categories',
					'No job article categories are selected for the generator. Please update the plugin settings.'
				);
			}
			$args['category__in'] = $categories;
		}

		$query = new WP_Query( $args );
		if ( empty( $query->posts ) ) {
			return new WP_Error(
				'no_articles',
				'No published job articles are available to display right now. Please try again later.'
			);
		}

		$post_id = (int) $query->posts[0];
		$url     = get_permalink( $post_id );
		if ( ! $url ) {
			return new WP_Error( 'no_articles', 'The selected article could not be opened.' );
		}

		return array(
			'id'  => $post_id,
			'url' => $url,
		);
	}

	/**
	 * Redirect the browser in the same tab to the article + opaque identifier.
	 *
	 * @param string $article_url Article URL.
	 * @param string $public_id   Opaque identifier.
	 * @return void
	 */
	public static function redirect_to( $article_url, $public_id ) {
		$target = add_query_arg( 'nzd_gen', rawurlencode( (string) $public_id ), (string) $article_url );

		if ( ! self::is_same_site_url( $target ) ) {
			self::fail( 'invalid_redirect', 500, 'The download generator could not build a safe redirect.' );
			return;
		}

		nocache_headers();
		header( 'Referrer-Policy: no-referrer' );
		header( 'X-Robots-Tag: noindex, nofollow, noarchive' );
		wp_redirect( $target, 302 );
		exit;
	}

	/**
	 * Emit a themed error page and stop.
	 *
	 * @param string $code    Machine code (for logging).
	 * @param int    $status  HTTP status.
	 * @param string $message Visitor-facing message.
	 * @return void
	 */
	public static function fail( $code, $status, $message ) {
		NZD_LG_Options::bump_stat( 'failed_requests' );
		NZD_LG_Plugin::log( 'entry rejected: ' . $code . ' (HTTP ' . (int) $status . ')' );
		nocache_headers();
		status_header( (int) $status );

		$settings   = NZD_LG_Options::get();
		$return_url = '' !== $settings['nzd_site_url'] ? $settings['nzd_site_url'] : home_url( '/' );

		$html  = '<div style="max-width:520px;margin:10vh auto;padding:28px;border:1px solid #e5e7eb;border-radius:12px;'
			. 'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;text-align:center;line-height:1.6">';
		$html .= '<div style="font-size:34px;line-height:1">&#9203;</div>';
		$html .= '<h1 style="font-size:20px;margin:12px 0 8px">Download link unavailable</h1>';
		$html .= '<p style="margin:0 0 20px;color:#374151">' . esc_html( $message ) . '</p>';
		$html .= '<p><a href="' . esc_url( $return_url ) . '" style="display:inline-block;padding:10px 18px;border-radius:999px;'
			. 'background:#111827;color:#fff;text-decoration:none">Return to NZDWorld</a></p>';
		$html .= '<p style="font-size:12px;color:#6b7280;margin-top:18px">Reference: ' . esc_html( $code ) . '</p>';
		$html .= '</div>';

		wp_die(
			$html,
			'Download link unavailable',
			array(
				'response'  => (int) $status,
				'back_link' => false,
			)
		);
	}

	/**
	 * Map an NZDWorld error code to an HTTP status.
	 *
	 * @param string $code Error code.
	 * @return int
	 */
	public static function status_for( $code ) {
		$map = array(
			'integration_disabled' => 403,
			'unauthorized'         => 403,
			'domain_mismatch'      => 403,
			'invalid_signature'    => 403,
			'insecure_transport'   => 403,
			'invalid_token'        => 404,
			'session_expired'      => 410,
			'session_redeemed'     => 410,
			'session_revoked'      => 410,
			'quality_unavailable'  => 410,
			'download_disabled'    => 410,
			'vip_required'         => 403,
			'rate_limited'         => 429,
			'network_error'        => 503,
			'server_error'         => 503,
			'countdown_too_long'   => 503,
		);
		return isset( $map[ $code ] ) ? (int) $map[ $code ] : 400;
	}

	/**
	 * Visitor-facing message for an API failure.
	 *
	 * @param array $result API result.
	 * @return string
	 */
	public static function visitor_message( $result ) {
		$code = isset( $result['code'] ) ? (string) $result['code'] : '';
		if ( 'network_error' === $code || 'server_error' === $code ) {
			return 'We could not reach NZDWorld to verify this download. Please try again in a moment.';
		}
		if ( ! empty( $result['message'] ) ) {
			return (string) $result['message'];
		}
		return 'This download link could not be verified. Please start again from NZDWorld.';
	}

	/**
	 * Parse an ISO-8601 timestamp from NZDWorld into a Unix timestamp.
	 *
	 * @param string $value Timestamp.
	 * @return int Unix timestamp or 0.
	 */
	public static function parse_time( $value ) {
		$value = trim( (string) $value );
		if ( '' === $value ) {
			return 0;
		}
		$timestamp = strtotime( $value );
		return false === $timestamp ? 0 : (int) $timestamp;
	}

	/**
	 * Simple per-IP limiter for the entry route.
	 *
	 * @return bool True when the request may proceed.
	 */
	public static function rate_limit_ok() {
		$limit = (int) apply_filters( 'nzd_lg_entry_rate_limit', 20 );
		$key   = 'nzd_lg_entry_' . md5( self::client_ip() );
		$hits  = (int) get_transient( $key );

		if ( $hits >= $limit ) {
			return false;
		}
		set_transient( $key, $hits + 1, 10 * MINUTE_IN_SECONDS );
		return true;
	}

	/**
	 * Remote IP address (never taken from a client-controlled header).
	 *
	 * @return string
	 */
	public static function client_ip() {
		$ip = isset( $_SERVER['REMOTE_ADDR'] ) ? wp_unslash( $_SERVER['REMOTE_ADDR'] ) : '';
		return filter_var( (string) $ip, FILTER_VALIDATE_IP ) ? (string) $ip : '0.0.0.0';
	}

	/**
	 * Current (or newly created) opaque visitor id.
	 *
	 * @param bool $create Whether to create one when missing.
	 * @return string
	 */
	public static function visitor_id( $create = false ) {
		$cookie = '';
		if ( isset( $_COOKIE[ self::VISITOR_COOKIE ] ) && is_string( $_COOKIE[ self::VISITOR_COOKIE ] ) ) {
			$cookie = (string) wp_unslash( $_COOKIE[ self::VISITOR_COOKIE ] );
		}
		if ( preg_match( '/^[a-f0-9]{32}$/', $cookie ) ) {
			return $cookie;
		}
		return $create ? bin2hex( random_bytes( 16 ) ) : '';
	}

	/**
	 * Set the visitor cookie (httponly, SameSite=Lax).
	 *
	 * @param string $visitor_id Visitor id.
	 * @return void
	 */
	public static function set_visitor_cookie( $visitor_id ) {
		if ( headers_sent() ) {
			$_COOKIE[ self::VISITOR_COOKIE ] = $visitor_id;
			return;
		}
		$path   = defined( 'COOKIEPATH' ) && COOKIEPATH ? COOKIEPATH : '/';
		$domain = defined( 'COOKIE_DOMAIN' ) && COOKIE_DOMAIN ? COOKIE_DOMAIN : '';
		setcookie(
			self::VISITOR_COOKIE,
			$visitor_id,
			array(
				'expires'  => time() + 2 * HOUR_IN_SECONDS,
				'path'     => $path,
				'domain'   => $domain,
				'secure'   => is_ssl(),
				'httponly' => true,
				'samesite' => 'Lax',
			)
		);
		$_COOKIE[ self::VISITOR_COOKIE ] = $visitor_id;
	}

	/**
	 * Whether a session belongs to this browser.
	 *
	 * The cookie is the primary binding. A cookie-less browser is accepted only
	 * within two minutes of the handoff and from the same network address, so a
	 * shared article URL cannot be redeemed by somebody else.
	 *
	 * @param array $session Session row.
	 * @return bool
	 */
	public static function session_matches_visitor( $session ) {
		if ( ! is_array( $session ) ) {
			return false;
		}
		$visitor = self::visitor_id( false );
		if ( '' !== $visitor && hash_equals( (string) $session['visitor_hash'], NZD_LG_Crypto::hash( $visitor ) ) ) {
			return true;
		}
		if ( isset( $session['ip_hash'] ) && hash_equals( (string) $session['ip_hash'], NZD_LG_Crypto::hash( self::client_ip() ) ) ) {
			return ( time() - (int) $session['created_at'] ) <= 120;
		}
		return false;
	}

	/**
	 * Whether a URL points at this WordPress site.
	 *
	 * @param string $url URL to test.
	 * @return bool
	 */
	public static function is_same_site_url( $url ) {
		$parts      = wp_parse_url( (string) $url );
		$home_parts = wp_parse_url( home_url( '/' ) );
		if ( ! is_array( $parts ) || ! is_array( $home_parts ) || empty( $parts['host'] ) ) {
			return false;
		}
		if ( strtolower( $parts['host'] ) !== strtolower( $home_parts['host'] ) ) {
			return false;
		}
		$scheme = isset( $parts['scheme'] ) ? strtolower( $parts['scheme'] ) : '';
		return in_array( $scheme, array( 'https', 'http' ), true );
	}
}

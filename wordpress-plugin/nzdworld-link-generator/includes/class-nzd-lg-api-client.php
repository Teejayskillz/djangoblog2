<?php
/**
 * Server-to-server client for the NZDWorld External Link Generator API.
 *
 * Every request is signed with HMAC-SHA256 over the timestamp, a single-use
 * nonce and the exact JSON body, using the API key as the HMAC secret. The key
 * never reaches the browser: it is only used inside this class on the server.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * Signed HTTPS client for NZDWorld.
 */
final class NZD_LG_Api_Client {

	/**
	 * NZDWorld base URL (no trailing slash).
	 *
	 * @var string
	 */
	private $base_url;

	/**
	 * API key.
	 *
	 * @var string
	 */
	private $api_key;

	/**
	 * This installation's URL.
	 *
	 * @var string
	 */
	private $site_url;

	/**
	 * Constructor.
	 *
	 * @param string $base_url NZDWorld URL.
	 * @param string $api_key  API key.
	 * @param string $site_url This site's URL (defaults to home_url).
	 */
	public function __construct( $base_url, $api_key, $site_url = '' ) {
		$this->base_url = untrailingslashit( (string) $base_url );
		$this->api_key  = (string) $api_key;
		$this->site_url = '' !== $site_url ? (string) $site_url : home_url( '/' );
	}

	/**
	 * Build a client from the stored settings.
	 *
	 * @return NZD_LG_Api_Client
	 */
	public static function from_settings() {
		$settings = NZD_LG_Options::get();
		return new self( $settings['nzd_site_url'], $settings['api_key'], home_url( '/' ) );
	}

	/**
	 * Whether a URL is HTTPS (or explicitly allowed for local development).
	 *
	 * @param string $url URL to check.
	 * @return bool
	 */
	public static function is_secure_url( $url ) {
		$parts = wp_parse_url( (string) $url );
		if ( ! is_array( $parts ) || empty( $parts['scheme'] ) ) {
			return false;
		}
		if ( 'https' === strtolower( $parts['scheme'] ) ) {
			return true;
		}
		/**
		 * Development escape hatch. Never enable this on a production site:
		 * the API key and session tokens would travel in clear text.
		 *
		 * @param bool   $allow Whether to allow plain HTTP.
		 * @param string $url   URL being checked.
		 */
		return (bool) apply_filters( 'nzd_lg_allow_insecure_transport', false, $url );
	}

	/**
	 * Compute the request signature.
	 *
	 * @param string $secret    API key.
	 * @param string $timestamp Unix timestamp as a string.
	 * @param string $nonce     Random nonce.
	 * @param string $body      Raw JSON body.
	 * @return string Hex digest.
	 */
	public static function sign( $secret, $timestamp, $nonce, $body ) {
		$payload = $timestamp . '.' . $nonce . '.' . hash( 'sha256', (string) $body );
		return hash_hmac( 'sha256', $payload, (string) $secret );
	}

	/**
	 * Full endpoint URL for an API path.
	 *
	 * @param string $path Relative path, e.g. "sessions/validate/".
	 * @return string
	 */
	public function endpoint( $path ) {
		return $this->base_url . '/api/external-link/' . ltrim( (string) $path, '/' );
	}

	/**
	 * The entry URL template reported to NZDWorld.
	 *
	 * Contains exactly one {token} placeholder. Pretty permalinks get a clean
	 * path; plain permalinks fall back to a query argument that always works.
	 *
	 * @return string
	 */
	public static function entry_template() {
		$home       = untrailingslashit( home_url( '/' ) );
		$permalinks = get_option( 'permalink_structure' );
		if ( ! empty( $permalinks ) ) {
			return $home . '/' . NZD_LG_ENTRY_SLUG . '/{token}/';
		}
		return $home . '/?nzdworld_token={token}';
	}

	/**
	 * Human readable message for an API error code.
	 *
	 * @param string $code Error code from NZDWorld.
	 * @return string
	 */
	public static function map_error( $code ) {
		$messages = array(
			'integration_disabled'  => 'The download generator is currently disabled on NZDWorld.',
			'invalid_token'         => 'This download link is not valid any more. Please start again from NZDWorld.',
			'session_expired'       => 'This download link has expired. Please start again from NZDWorld.',
			'session_redeemed'      => 'This download link has already been used. Please start again from NZDWorld.',
			'session_revoked'       => 'This download link was cancelled. Please start again from NZDWorld.',
			'session_not_validated' => 'This download session is incomplete. Please start again from NZDWorld.',
			'too_early'             => 'Please wait for the countdown to finish.',
			'quality_unavailable'   => 'That download is no longer available on NZDWorld.',
			'download_disabled'     => 'Downloads are currently disabled for that item on NZDWorld.',
			'vip_required'          => 'That download requires a VIP subscription on NZDWorld.',
			'countdown_too_long'    => 'The countdown is longer than NZDWorld allows. Lower it in the plugin settings.',
			'domain_mismatch'       => 'This WordPress site is not the one registered with NZDWorld.',
			'unauthorized'          => 'The API key was rejected by NZDWorld. Generate a new key and save it here.',
			'invalid_signature'     => 'The request signature was rejected by NZDWorld. Check the server clock.',
			'stale_request'         => 'The server clock is out of sync with NZDWorld. Please fix the time and retry.',
			'replayed_request'      => 'A duplicate request was rejected. Please retry.',
			'insecure_transport'    => 'NZDWorld requires an HTTPS connection.',
			'rate_limited'          => 'Too many requests. Please try again in a minute.',
			'invalid_entry_url'     => 'NZDWorld rejected this site\'s entry URL. Re-save the settings and test again.',
			'invalid_site'          => 'NZDWorld rejected this site URL. It must be an absolute HTTPS URL.',
			'invalid_json'          => 'NZDWorld could not read the request.',
			'network_error'         => 'Could not reach NZDWorld. Please try again in a moment.',
			'server_error'          => 'NZDWorld returned an error. Please try again in a moment.',
			'invalid_response'      => 'NZDWorld returned an unexpected response.',
			'not_configured'        => 'The link generator is not configured yet.',
			'premium_not_allowed'   => 'Premium downloads must be started from NZDWorld.',
		);
		if ( isset( $messages[ $code ] ) ) {
			return $messages[ $code ];
		}
		return 'The download generator could not complete the request. Please try again.';
	}

	/**
	 * Perform a signed POST request.
	 *
	 * @param string $path    API path.
	 * @param array  $payload Request body.
	 * @return array {ok, status, data, code, message}
	 */
	private function request( $path, $payload ) {
		if ( '' === $this->base_url || '' === $this->api_key ) {
			return $this->failure( 'not_configured', 0 );
		}
		if ( ! self::is_secure_url( $this->base_url ) ) {
			return $this->failure( 'insecure_transport', 0 );
		}

		$payload['site_url']        = $this->site_url;
		$payload['plugin_version']  = NZD_LG_VERSION;
		$payload['site_name']       = wp_specialchars_decode( get_bloginfo( 'name' ), ENT_QUOTES );
		$body                       = wp_json_encode( $payload );
		if ( false === $body || '' === $body ) {
			return $this->failure( 'invalid_json', 0 );
		}
		$timestamp                  = (string) time();
		$nonce                      = bin2hex( random_bytes( 16 ) );

		$args = array(
			'timeout'             => 20,
			'redirection'         => 0,
			'sslverify'           => true,
			'reject_unsafe_urls'  => true,
			'limit_response_size' => 262144,
			'user-agent'          => 'NZDWorld-Link-Generator/' . NZD_LG_VERSION . '; ' . home_url( '/' ),
			'headers'             => array(
				'Content-Type'   => 'application/json',
				'Accept'         => 'application/json',
				'Authorization'  => 'Bearer ' . $this->api_key,
				'X-NZD-Timestamp' => $timestamp,
				'X-NZD-Nonce'     => $nonce,
				'X-NZD-Signature' => self::sign( $this->api_key, $timestamp, $nonce, $body ),
			),
			'body'                => $body,
		);

		$response = wp_remote_post( $this->endpoint( $path ), $args );

		if ( is_wp_error( $response ) ) {
			return $this->failure( 'network_error', 0, $response->get_error_message() );
		}

		$status = (int) wp_remote_retrieve_response_code( $response );
		$raw    = (string) wp_remote_retrieve_body( $response );
		$data   = json_decode( $raw, true );

		if ( ! is_array( $data ) ) {
			$code = $status >= 500 ? 'server_error' : 'invalid_response';
			return $this->failure( $code, $status );
		}

		if ( ! empty( $data['ok'] ) ) {
			return array(
				'ok'      => true,
				'status'  => $status,
				'data'    => $data,
				'code'    => '',
				'message' => '',
			);
		}

		$error = isset( $data['error'] ) && is_array( $data['error'] ) ? $data['error'] : array();
		$code  = isset( $error['code'] ) ? sanitize_key( $error['code'] ) : 'invalid_response';

		if ( 429 === $status && '' === $code ) {
			$code = 'rate_limited';
		}
		if ( 401 === $status && '' === $code ) {
			$code = 'unauthorized';
		}

		return array(
			'ok'       => false,
			'status'   => $status,
			'data'     => $data,
			'code'     => $code,
			'message'  => self::map_error( $code ),
			'retry_after' => isset( $error['retry_after'] ) ? (int) $error['retry_after'] : 0,
		);
	}

	/**
	 * Build a failure result without touching the network.
	 *
	 * @param string $code    Error code.
	 * @param int    $status  HTTP status (0 when unknown).
	 * @param string $technical Optional technical detail (never shown raw to visitors).
	 * @return array
	 */
	private function failure( $code, $status, $technical = '' ) {
		return array(
			'ok'          => false,
			'status'      => (int) $status,
			'data'        => array(),
			'code'        => (string) $code,
			'message'     => self::map_error( $code ),
			'technical'   => (string) $technical,
			'retry_after' => 0,
		);
	}

	/**
	 * Connection test / installation registration.
	 *
	 * @return array
	 */
	public function connection_test() {
		$settings = NZD_LG_Options::get();
		return $this->request(
			'connection-test/',
			array(
				'entry_url_template' => self::entry_template(),
				'countdown_seconds'  => (int) $settings['countdown_seconds'],
			)
		);
	}

	/**
	 * Validate an incoming session token.
	 *
	 * @param string $token     Opaque NZDWorld token.
	 * @param int    $countdown Countdown seconds configured here.
	 * @return array
	 */
	public function validate( $token, $countdown ) {
		return $this->request(
			'sessions/validate/',
			array(
				'token'             => (string) $token,
				'countdown_seconds' => (int) $countdown,
				'entry_url_template' => self::entry_template(),
			)
		);
	}

	/**
	 * Redeem a validated session and receive the destination URL.
	 *
	 * @param string $token Opaque NZDWorld token.
	 * @return array
	 */
	public function redeem( $token ) {
		return $this->request(
			'sessions/redeem/',
			array(
				'token' => (string) $token,
			)
		);
	}

	/**
	 * The destination URL from a successful redeem response.
	 *
	 * @param array $result Result from redeem().
	 * @return string '' when missing/invalid.
	 */
	public static function destination_from( $result ) {
		if ( empty( $result['ok'] ) || ! isset( $result['data']['destination_url'] ) ) {
			return '';
		}
		$url   = (string) $result['data']['destination_url'];
		$parts = wp_parse_url( $url );
		if ( ! is_array( $parts ) || empty( $parts['scheme'] ) || empty( $parts['host'] ) ) {
			return '';
		}
		if ( ! in_array( strtolower( $parts['scheme'] ), array( 'https', 'http' ), true ) ) {
			return '';
		}
		if ( isset( $parts['user'] ) || isset( $parts['pass'] ) ) {
			return '';
		}
		return $url;
	}
}

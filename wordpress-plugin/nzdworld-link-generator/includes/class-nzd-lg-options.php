<?php
/**
 * Settings, statistics and small sanitising helpers.
 *
 * The API key lives only in the WordPress options table and is never printed to
 * the frontend. All options are read through this class so defaults, clamping
 * and upgrades stay in one place.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * Option/statistics access and sanitising helpers.
 */
final class NZD_LG_Options {

	/**
	 * Default settings.
	 *
	 * @return array
	 */
	public static function defaults() {
		return array(
			'enabled'              => 0,
			'nzd_site_url'         => '',
			'blog_page_url'        => '',
			'api_key'              => '',
			'api_key_last4'        => '',
			'countdown_seconds'    => 20,
			'random_article'       => 1,
			'use_all_posts'        => 1,
			'allowed_categories'   => array(),
			'popup_heading'        => 'Generating your download link',
			'popup_description'    => 'Please wait while we prepare your link.',
			'popup_button_text'    => 'Click to Proceed',
			'popup_logo_url'       => '',
			'session_ttl'          => 900,
			'connection_status'    => 'unknown',
			'connection_message'   => '',
			'last_success'         => '',
			'max_session_seconds'  => 900,
			'min_countdown_seconds' => 5,
			'max_countdown_seconds' => 120,
			'plugin_version'       => NZD_LG_VERSION,
		);
	}

	/**
	 * Current settings merged over the defaults.
	 *
	 * @return array
	 */
	public static function get() {
		$stored = get_option( NZD_LG_OPTION, array() );
		if ( ! is_array( $stored ) ) {
			$stored = array();
		}
		return wp_parse_args( $stored, self::defaults() );
	}

	/**
	 * Persist a full settings array.
	 *
	 * @param array $settings Settings to store.
	 * @return array The stored settings.
	 */
	public static function update( $settings ) {
		$clean = wp_parse_args( (array) $settings, self::defaults() );
		update_option( NZD_LG_OPTION, $clean, false );
		return $clean;
	}

	/**
	 * Whether the generator is switched on.
	 *
	 * @return bool
	 */
	public static function is_enabled() {
		$settings = self::get();
		return ! empty( $settings['enabled'] );
	}

	/**
	 * Whether the minimum configuration for talking to NZDWorld exists.
	 *
	 * @return bool
	 */
	public static function is_configured() {
		$settings = self::get();
		return '' !== $settings['nzd_site_url'] && '' !== $settings['api_key'];
	}

	/**
	 * NZDWorld base URL without a trailing slash.
	 *
	 * @return string
	 */
	public static function nzd_url() {
		$settings = self::get();
		return untrailingslashit( (string) $settings['nzd_site_url'] );
	}

	/**
	 * The blog page used for routing/popup detection (falls back to home URL).
	 *
	 * @return string
	 */
	public static function blog_url() {
		$settings = self::get();
		if ( '' !== $settings['blog_page_url'] ) {
			return (string) $settings['blog_page_url'];
		}
		return home_url( '/' );
	}

	/**
	 * Record the outcome of a connection test.
	 *
	 * @param string $status       Machine status: connected|disabled|error|unknown.
	 * @param string $message      Human readable message.
	 * @param int    $max_lifetime Maximum session lifetime reported by NZDWorld.
	 * @return void
	 */
	public static function set_connection_state( $status, $message = '', $max_lifetime = 0 ) {
		$settings = self::get();
		$settings['connection_status']  = (string) $status;
		$settings['connection_message'] = (string) $message;
		if ( $max_lifetime > 0 ) {
			$settings['max_session_seconds'] = (int) $max_lifetime;
		}
		if ( 'connected' === $status || 'disabled' === $status ) {
			$settings['last_success'] = gmdate( 'Y-m-d H:i:s' );
		}
		self::update( $settings );
	}

	/**
	 * Remember the installation details reported by NZDWorld.
	 *
	 * @param array $payload Connection test response.
	 * @return void
	 */
	public static function remember_limits( $payload ) {
		$settings = self::get();
		if ( isset( $payload['session_ttl_seconds'] ) ) {
			$settings['max_session_seconds'] = max( 120, (int) $payload['session_ttl_seconds'] );
		}
		if ( isset( $payload['min_countdown_seconds'] ) ) {
			$settings['min_countdown_seconds'] = max( 1, (int) $payload['min_countdown_seconds'] );
		}
		if ( isset( $payload['max_countdown_seconds'] ) ) {
			$settings['max_countdown_seconds'] = max( 1, (int) $payload['max_countdown_seconds'] );
		}
		self::update( $settings );
	}

	/**
	 * Statistics array.
	 *
	 * @return array
	 */
	public static function stats() {
		$stored = get_option( NZD_LG_STATS_OPTION, array() );
		if ( ! is_array( $stored ) ) {
			$stored = array();
		}
		return wp_parse_args(
			$stored,
			array(
				'received_requests'      => 0,
				'successful_generations' => 0,
				'expired_sessions'       => 0,
				'failed_requests'        => 0,
				'last_request_at'        => '',
			)
		);
	}

	/**
	 * Increment one statistics counter atomically-ish.
	 *
	 * @param string $key Counter name.
	 * @param int    $by  Amount.
	 * @return void
	 */
	public static function bump_stat( $key, $by = 1 ) {
		$allowed = array(
			'received_requests',
			'successful_generations',
			'expired_sessions',
			'failed_requests',
		);
		if ( ! in_array( $key, $allowed, true ) ) {
			return;
		}
		$stats               = self::stats();
		$stats[ $key ]       = max( 0, (int) $stats[ $key ] ) + (int) $by;
		$stats['last_request_at'] = gmdate( 'Y-m-d H:i:s' );
		update_option( NZD_LG_STATS_OPTION, $stats, false );
	}

	/**
	 * Run one-off upgrades (defaults, database table) after a plugin update.
	 *
	 * @return void
	 */
	public static function maybe_upgrade() {
		$installed = get_option( 'nzd_lg_version', '' );
		if ( NZD_LG_VERSION === $installed ) {
			return;
		}
		if ( ! get_option( NZD_LG_OPTION ) ) {
			self::update( self::defaults() );
		} else {
			$settings                 = self::get();
			$settings['plugin_version'] = NZD_LG_VERSION;
			self::update( $settings );
		}
		NZD_LG_Sessions::install();
		update_option( 'nzd_lg_version', NZD_LG_VERSION, false );
	}

	/**
	 * Validate/normalise an absolute HTTPS URL.
	 *
	 * @param string $value Raw value.
	 * @return string Clean URL or '' when invalid.
	 */
	public static function clean_url( $value ) {
		$value = trim( (string) $value );
		if ( '' === $value ) {
			return '';
		}
		$url = esc_url_raw( $value, array( 'https', 'http' ) );
		if ( '' === $url ) {
			return '';
		}
		$parts = wp_parse_url( $url );
		if ( ! is_array( $parts ) || empty( $parts['host'] ) ) {
			return '';
		}
		if ( ! isset( $parts['scheme'] ) || 'https' !== strtolower( $parts['scheme'] ) ) {
			return '';
		}
		if ( isset( $parts['user'] ) || isset( $parts['pass'] ) ) {
			return '';
		}
		return untrailingslashit( $url );
	}

	/**
	 * Restrict a URL to the site's own host (prevents admin-config open redirects).
	 *
	 * @param string $value Raw value.
	 * @return string Clean same-site URL or ''.
	 */
	public static function clean_same_site_url( $value ) {
		$value = trim( (string) $value );
		if ( '' === $value ) {
			return '';
		}
		$url = esc_url_raw( $value, array( 'https', 'http' ) );
		if ( '' === $url ) {
			return '';
		}
		$parts       = wp_parse_url( $url );
		$home_parts  = wp_parse_url( home_url( '/' ) );
		if ( ! is_array( $parts ) || empty( $parts['host'] ) || ! is_array( $home_parts ) ) {
			return '';
		}
		if ( strtolower( $parts['host'] ) !== strtolower( $home_parts['host'] ) ) {
			return '';
		}
		if ( isset( $parts['user'] ) || isset( $parts['pass'] ) ) {
			return '';
		}
		$scheme = isset( $parts['scheme'] ) ? strtolower( $parts['scheme'] ) : 'https';
		if ( ! in_array( $scheme, array( 'http', 'https' ), true ) ) {
			return '';
		}
		$port = isset( $parts['port'] ) ? ':' . (int) $parts['port'] : '';
		$path = isset( $parts['path'] ) ? $parts['path'] : '/';

		// Rebuild from the submitted host/path instead of home_url( $path ):
		// home_url() already contains the install sub-directory, so passing an
		// absolute path into it would duplicate the directory on sub-folder
		// installs.
		return untrailingslashit( $scheme . '://' . $parts['host'] . $port . $path );
	}

	/**
	 * Multibyte-safe truncation (mbstring is optional on some hosts).
	 *
	 * @param string $value Text.
	 * @param int    $limit Maximum characters.
	 * @return string
	 */
	public static function trim_text( $value, $limit ) {
		$value = (string) $value;
		$limit = (int) $limit;
		if ( $limit < 1 ) {
			return '';
		}
		if ( function_exists( 'mb_substr' ) ) {
			return mb_substr( $value, 0, $limit );
		}
		return substr( $value, 0, $limit );
	}

	/**
	 * Clamp an integer into a range.
	 *
	 * @param mixed $value   Raw value.
	 * @param int   $min     Minimum.
	 * @param int   $max     Maximum.
	 * @param int   $default Fallback when the value is not numeric.
	 * @return int
	 */
	public static function clamp_int( $value, $min, $max, $default ) {
		if ( ! is_numeric( $value ) ) {
			return (int) $default;
		}
		$number = (int) $value;
		if ( $number < $min ) {
			return (int) $min;
		}
		if ( $number > $max ) {
			return (int) $max;
		}
		return $number;
	}

	/**
	 * Masked representation of the stored API key (never the key itself).
	 *
	 * @return string
	 */
	public static function masked_key() {
		$settings = self::get();
		if ( '' === $settings['api_key'] ) {
			return '';
		}
		$suffix = '' !== $settings['api_key_last4'] ? $settings['api_key_last4'] : substr( (string) $settings['api_key'], -4 );
		return str_repeat( "\xE2\x80\xA2", 10 ) . ' ' . $suffix;
	}
}

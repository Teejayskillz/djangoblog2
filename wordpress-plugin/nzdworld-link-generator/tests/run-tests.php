<?php
/**
 * Standalone logic tests for the NZDWorld Link Generator plugin.
 *
 * Run from the plugin directory:
 *
 *     php tests/run-tests.php
 *
 * These tests do not need WordPress: a minimal set of WordPress functions is
 * stubbed below so the pure logic (URL validation, signing, crypto, token
 * formats, error mapping) can be exercised on any PHP 7.4+ CLI. Integration
 * behaviour (rewrite rules, WP_Query, AJAX) still has to be verified on a real
 * WordPress install - see TEST-REPORT.md for the manual checklist.
 *
 * @package NZDWorld_Link_Generator
 */

// phpcs:disable

define( 'ABSPATH', __DIR__ . '/../' );
define( 'NZD_LG_VERSION', '1.0.0' );
define( 'NZD_LG_FILE', dirname( __DIR__ ) . '/nzdworld-link-generator.php' );
define( 'NZD_LG_DIR', dirname( __DIR__ ) . '/' );
define( 'NZD_LG_URL', 'https://blog.example.com/wp-content/plugins/nzdworld-link-generator/' );
define( 'NZD_LG_OPTION', 'nzd_lg_settings' );
define( 'NZD_LG_STATS_OPTION', 'nzd_lg_stats' );
define( 'NZD_LG_CRON_HOOK', 'nzd_lg_cleanup' );
define( 'NZD_LG_ENTRY_SLUG', 'nzdworld-link' );
define( 'MINUTE_IN_SECONDS', 60 );
define( 'HOUR_IN_SECONDS', 3600 );

$GLOBALS['nzd_test_permalinks'] = '/%postname%/';
$GLOBALS['nzd_test_home']       = 'https://blog.example.com';

// --- Minimal WordPress stubs ------------------------------------------------
function home_url( $path = '' ) {
	return rtrim( $GLOBALS['nzd_test_home'], '/' ) . ( '' === $path ? '/' : '/' . ltrim( $path, '/' ) );
}
function get_option( $name, $default = false ) {
	if ( 'permalink_structure' === $name ) {
		return $GLOBALS['nzd_test_permalinks'];
	}
	return $default;
}
function update_option( $name, $value, $autoload = null ) {
	return true;
}
function wp_salt( $scheme = 'auth' ) {
	return 'test-salt-' . $scheme . '-for-harness';
}
function get_bloginfo( $show = '' ) {
	return 'Test Blog';
}
function wp_specialchars_decode( $string, $quote_style = ENT_NOQUOTES ) {
	return htmlspecialchars_decode( (string) $string, $quote_style );
}
function apply_filters( $tag, $value ) {
	return $value;
}
function sanitize_text_field( $value ) {
	return trim( strip_tags( (string) $value ) );
}
function sanitize_key( $value ) {
	return preg_replace( '/[^a-z0-9_\-]/', '', strtolower( (string) $value ) );
}
function sanitize_textarea_field( $value ) {
	return trim( strip_tags( (string) $value ) );
}
function untrailingslashit( $value ) {
	return rtrim( (string) $value, '/' );
}
function trailingslashit( $value ) {
	return rtrim( (string) $value, '/' ) . '/';
}
function is_ssl() {
	return true;
}
function __( $text, $domain = null ) {
	return $text;
}
function wp_parse_url( $url, $component = -1 ) {
	return -1 === $component ? parse_url( $url ) : parse_url( $url, $component );
}
function esc_url_raw( $url, $protocols = null ) {
	$url = trim( (string) $url );
	if ( '' === $url ) {
		return '';
	}
	if ( preg_match( '/[\s<>"\']/', $url ) ) {
		return '';
	}
	$parts = parse_url( $url );
	if ( ! is_array( $parts ) || empty( $parts['scheme'] ) || empty( $parts['host'] ) ) {
		return '';
	}
	$scheme = strtolower( $parts['scheme'] );
	if ( is_array( $protocols ) && ! in_array( $scheme, $protocols, true ) ) {
		return '';
	}
	if ( ! in_array( $scheme, array( 'http', 'https' ), true ) ) {
		return '';
	}
	return $url;
}

require_once NZD_LG_DIR . 'includes/class-nzd-lg-options.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-crypto.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-sessions.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-api-client.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-entry.php';

// --- Tiny assertion runner --------------------------------------------------
$GLOBALS['nzd_test_failures'] = 0;
$GLOBALS['nzd_test_count']    = 0;

function nzd_check( $label, $condition ) {
	$GLOBALS['nzd_test_count']++;
	if ( $condition ) {
		echo "PASS  {$label}\n";
		return;
	}
	$GLOBALS['nzd_test_failures']++;
	echo "FAIL  {$label}\n";
}

// 1. HMAC signature: fixed vector shared with the Django test suite.
nzd_check(
	'signature matches the cross-language test vector',
	'a66ff43b5e973bedb09598f2ad37b2cd88eed5deb466c865cd77f3b52dc76775'
		=== NZD_LG_Api_Client::sign(
			'nzd_live_test_key_ABC123',
			'1700000000',
			'abc123def456',
			'{"site_url":"https://blog.example.com","token":"abc"}'
		)
);

// 2. Error mapping.
nzd_check( 'known error has a message', '' !== NZD_LG_Api_Client::map_error( 'session_expired' ) );
nzd_check( 'unknown error falls back to a generic message', '' !== NZD_LG_Api_Client::map_error( 'something_new' ) );

// 3. Destination validation (no open redirects, no javascript: URLs).
nzd_check( 'destination accepts https URLs', 'https://cdn.example.com/a.mp4' === NZD_LG_Api_Client::destination_from(
	array(
		'ok'   => true,
		'data' => array( 'destination_url' => 'https://cdn.example.com/a.mp4' ),
	)
) );
nzd_check( 'destination rejects javascript: URLs', '' === NZD_LG_Api_Client::destination_from(
	array(
		'ok'   => true,
		'data' => array( 'destination_url' => 'javascript:alert(1)' ),
	)
) );
nzd_check( 'destination rejects credentials in URLs', '' === NZD_LG_Api_Client::destination_from(
	array(
		'ok'   => true,
		'data' => array( 'destination_url' => 'https://user:pass@cdn.example.com/a.mp4' ),
	)
) );
nzd_check( 'destination rejects relative URLs', '' === NZD_LG_Api_Client::destination_from(
	array(
		'ok'   => true,
		'data' => array( 'destination_url' => '/files/a.mp4' ),
	)
) );

// 4. Entry URL template.
$pretty = NZD_LG_Api_Client::entry_template();
nzd_check( 'pretty entry template is https and has one token', 1 === substr_count( $pretty, '{token}' ) && 0 === strpos( $pretty, 'https://' ) );
nzd_check( 'pretty entry template uses the entry slug', false !== strpos( $pretty, '/nzdworld-link/' ) );

$GLOBALS['nzd_test_permalinks'] = '';
$plain                          = NZD_LG_Api_Client::entry_template();
$GLOBALS['nzd_test_permalinks'] = '/%postname%/';
nzd_check( 'plain entry template uses the query fallback', false !== strpos( $plain, 'nzdworld_token={token}' ) );

// 5. Token format.
nzd_check( 'token pattern accepts a 43 char urlsafe token', 1 === preg_match( NZD_LG_Entry::TOKEN_PATTERN, str_repeat( 'a', 43 ) ) );
nzd_check( 'token pattern rejects short tokens', 0 === preg_match( NZD_LG_Entry::TOKEN_PATTERN, 'abc' ) );
nzd_check( 'token pattern rejects spaces', 0 === preg_match( NZD_LG_Entry::TOKEN_PATTERN, str_repeat( 'a', 40 ) . ' ' ) );

// 6. Timestamp parsing.
nzd_check( 'parse_time reads UTC ISO timestamps', 1700000000 === NZD_LG_Entry::parse_time( '2023-11-14T22:13:20Z' ) );
nzd_check( 'parse_time returns 0 for empty values', 0 === NZD_LG_Entry::parse_time( '' ) );
nzd_check( 'parse_time returns 0 for garbage', 0 === NZD_LG_Entry::parse_time( 'not-a-date' ) );

// 7. Status mapping.
nzd_check( 'expired sessions map to 410', 410 === NZD_LG_Entry::status_for( 'session_expired' ) );
nzd_check( 'unknown API errors map to 400', 400 === NZD_LG_Entry::status_for( 'never_seen' ) );

// 8. Integer clamping.
nzd_check( 'clamp_int applies the minimum', 5 === NZD_LG_Options::clamp_int( 1, 5, 120, 20 ) );
nzd_check( 'clamp_int applies the maximum', 120 === NZD_LG_Options::clamp_int( 9999, 5, 120, 20 ) );
nzd_check( 'clamp_int uses the default for non-numbers', 20 === NZD_LG_Options::clamp_int( 'abc', 5, 120, 20 ) );

// 9. URL sanitising.
nzd_check( 'clean_url accepts https', 'https://nzdworld.com' === NZD_LG_Options::clean_url( 'https://nzdworld.com/' ) );
nzd_check( 'clean_url rejects http', '' === NZD_LG_Options::clean_url( 'http://nzdworld.com' ) );
nzd_check( 'clean_url rejects credentials', '' === NZD_LG_Options::clean_url( 'https://user:pass@nzdworld.com' ) );
nzd_check( 'clean_url rejects relative values', '' === NZD_LG_Options::clean_url( '/relative' ) );

// 10. Crypto round trip and tamper detection.
if ( NZD_LG_Crypto::available() ) {
	$plaintext  = 'opaque-session-token-value';
	$ciphertext = NZD_LG_Crypto::encrypt( $plaintext );
	nzd_check( 'crypto encrypts to a non-empty payload', '' !== $ciphertext && false === strpos( $ciphertext, $plaintext ) );
	nzd_check( 'crypto decrypts back to the token', $plaintext === NZD_LG_Crypto::decrypt( $ciphertext ) );

	$tampered = base64_decode( $ciphertext, true );
	$tampered[ strlen( $tampered ) - 1 ] = chr( ord( $tampered[ strlen( $tampered ) - 1 ] ) ^ 0x01 );
	nzd_check( 'crypto rejects tampered payloads', '' === NZD_LG_Crypto::decrypt( base64_encode( $tampered ) ) );
} else {
	echo "SKIP  crypto tests (OpenSSL not available)\n";
}

// 11. Hashing helper.
$hash = NZD_LG_Crypto::hash( '203.0.113.10' );
nzd_check( 'hash is 64 hex characters', 64 === strlen( $hash ) && ctype_xdigit( $hash ) );
nzd_check( 'hash is stable', $hash === NZD_LG_Crypto::hash( '203.0.113.10' ) );
nzd_check( 'hash differs per input', $hash !== NZD_LG_Crypto::hash( '203.0.113.11' ) );

// 12. Public ids.
nzd_check( 'public id is 32 lowercase hex characters', 1 === preg_match( '/^[a-f0-9]{32}$/', NZD_LG_Sessions::new_public_id() ) );
nzd_check( 'two public ids differ', NZD_LG_Sessions::new_public_id() !== NZD_LG_Sessions::new_public_id() );

// 13. Same-site checks (redirect safety).
nzd_check( 'same-site URL accepted', NZD_LG_Entry::is_same_site_url( 'https://blog.example.com/jobs/apply/' ) );
nzd_check( 'foreign host rejected', ! NZD_LG_Entry::is_same_site_url( 'https://evil.example.net/jobs/' ) );
nzd_check( 'scheme-less value rejected', ! NZD_LG_Entry::is_same_site_url( '//blog.example.com/jobs/' ) );

// 14. Visitor message never leaks internals for network failures.
$message = NZD_LG_Entry::visitor_message( array( 'code' => 'network_error', 'message' => 'cURL error 28' ) );
nzd_check( 'network failures use a friendly message', false === strpos( $message, 'cURL' ) );

echo "\n" . $GLOBALS['nzd_test_count'] . " checks, " . $GLOBALS['nzd_test_failures'] . " failure(s).\n";
exit( $GLOBALS['nzd_test_failures'] > 0 ? 1 : 0 );

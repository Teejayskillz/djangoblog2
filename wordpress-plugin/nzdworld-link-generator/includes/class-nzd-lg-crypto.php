<?php
/**
 * Symmetric encryption for the NZDWorld session token at rest.
 *
 * The plugin needs the plaintext token later (server-to-server redemption), so
 * the token cannot be hashed. Instead it is encrypted with AES-256-GCM using a
 * key derived from the site's WordPress salts; a database leak alone does not
 * expose usable tokens. The key never leaves the server.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * AES-256-GCM helper (OpenSSL).
 */
final class NZD_LG_Crypto {

	const CIPHER = 'aes-256-gcm';
	const IV_LEN = 12;
	const TAG_LEN = 16;

	/**
	 * Whether the required OpenSSL functions are available.
	 *
	 * @return bool
	 */
	public static function available() {
		return function_exists( 'openssl_encrypt' )
			&& function_exists( 'openssl_decrypt' )
			&& in_array( self::CIPHER, openssl_get_cipher_methods(), true );
	}

	/**
	 * Derive the encryption key from the WordPress salts and site URL.
	 *
	 * @return string 32 raw bytes.
	 */
	private static function key() {
		$material = wp_salt( 'auth' ) . '|nzdworld-link-generator|' . home_url( '/' );
		return hash( 'sha256', $material, true );
	}

	/**
	 * Encrypt a value.
	 *
	 * @param string $plaintext Value to encrypt.
	 * @return string Base64 payload (iv|tag|ciphertext) or '' on failure.
	 */
	public static function encrypt( $plaintext ) {
		$plaintext = (string) $plaintext;
		if ( '' === $plaintext || ! self::available() ) {
			return '';
		}
		$iv  = random_bytes( self::IV_LEN );
		$tag = '';
		$raw = openssl_encrypt(
			$plaintext,
			self::CIPHER,
			self::key(),
			OPENSSL_RAW_DATA,
			$iv,
			$tag,
			'',
			self::TAG_LEN
		);
		if ( false === $raw || strlen( $tag ) !== self::TAG_LEN ) {
			return '';
		}
		return base64_encode( $iv . $tag . $raw );
	}

	/**
	 * Decrypt a value produced by encrypt().
	 *
	 * @param string $payload Base64 payload.
	 * @return string Plaintext or '' when the payload is invalid.
	 */
	public static function decrypt( $payload ) {
		$payload = (string) $payload;
		if ( '' === $payload || ! self::available() ) {
			return '';
		}
		$raw = base64_decode( $payload, true );
		if ( false === $raw || strlen( $raw ) < ( self::IV_LEN + self::TAG_LEN + 1 ) ) {
			return '';
		}
		$iv      = substr( $raw, 0, self::IV_LEN );
		$tag     = substr( $raw, self::IV_LEN, self::TAG_LEN );
		$cipher  = substr( $raw, self::IV_LEN + self::TAG_LEN );
		$plain   = openssl_decrypt(
			$cipher,
			self::CIPHER,
			self::key(),
			OPENSSL_RAW_DATA,
			$iv,
			$tag
		);
		return false === $plain ? '' : $plain;
	}

	/**
	 * Hash a visitor/network identifier for storage.
	 *
	 * @param string $value Raw value (IP address or visitor id).
	 * @return string 64 hex characters.
	 */
	public static function hash( $value ) {
		return hash_hmac( 'sha256', (string) $value, wp_salt( 'nonce' ) );
	}
}

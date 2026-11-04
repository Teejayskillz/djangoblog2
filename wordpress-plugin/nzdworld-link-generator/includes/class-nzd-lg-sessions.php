<?php
/**
 * Storage for the plugin's own generation sessions.
 *
 * A row exists only after NZDWorld validated an incoming token, and it is bound
 * to the article that will show the popup, to the visitor's browser (hashed
 * cookie) and to the NZDWorld session token (encrypted). The popup is rendered
 * only when a row matches the current request, so ordinary article visits never
 * show it.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'ABSPATH' ) || exit;

/**
 * Database access for generation sessions.
 */
final class NZD_LG_Sessions {

	const STATUS_READY    = 'ready';
	const STATUS_REDEEMED = 'redeemed';
	const STATUS_EXPIRED  = 'expired';
	const STATUS_FAILED   = 'failed';

	/**
	 * Fully qualified table name.
	 *
	 * @return string
	 */
	public static function table() {
		global $wpdb;
		return $wpdb->prefix . 'nzd_generator_sessions';
	}

	/**
	 * Create/upgrade the table with dbDelta.
	 *
	 * @return void
	 */
	public static function install() {
		global $wpdb;

		require_once ABSPATH . 'wp-admin/includes/upgrade.php';

		$table           = self::table();
		$charset_collate = $wpdb->get_charset_collate();

		$sql = "CREATE TABLE {$table} (
			id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
			public_id char(32) NOT NULL,
			token_hash char(64) NOT NULL,
			token_cipher text NOT NULL,
			article_id bigint(20) unsigned NOT NULL DEFAULT 0,
			article_url text NOT NULL,
			visitor_hash char(64) NOT NULL DEFAULT '',
			ip_hash char(64) NOT NULL DEFAULT '',
			status varchar(20) NOT NULL DEFAULT 'ready',
			created_at bigint(20) unsigned NOT NULL DEFAULT 0,
			not_before bigint(20) unsigned NOT NULL DEFAULT 0,
			expires_at bigint(20) unsigned NOT NULL DEFAULT 0,
			redeemed_at bigint(20) unsigned NOT NULL DEFAULT 0,
			last_seen bigint(20) unsigned NOT NULL DEFAULT 0,
			PRIMARY KEY  (id),
			UNIQUE KEY public_id (public_id),
			KEY token_hash (token_hash),
			KEY status_expires (status,expires_at)
		) {$charset_collate};";

		dbDelta( $sql );
	}

	/**
	 * Create a session row.
	 *
	 * @param array $data Session fields.
	 * @return string Public id on success, '' on failure.
	 */
	public static function create( $data ) {
		global $wpdb;

		$public_id = isset( $data['public_id'] ) ? $data['public_id'] : bin2hex( random_bytes( 16 ) );
		$row       = array(
			'public_id'    => $public_id,
			'token_hash'   => (string) $data['token_hash'],
			'token_cipher' => (string) $data['token_cipher'],
			'article_id'   => (int) $data['article_id'],
			'article_url'  => (string) $data['article_url'],
			'visitor_hash' => (string) $data['visitor_hash'],
			'ip_hash'      => (string) $data['ip_hash'],
			'status'       => self::STATUS_READY,
			'created_at'   => (int) $data['created_at'],
			'not_before'   => (int) $data['not_before'],
			'expires_at'   => (int) $data['expires_at'],
			'redeemed_at'  => 0,
			'last_seen'    => time(),
		);

		$inserted = $wpdb->insert(
			self::table(),
			$row,
			array( '%s', '%s', '%s', '%d', '%s', '%s', '%s', '%s', '%d', '%d', '%d', '%d', '%d' )
		);

		return $inserted ? $public_id : '';
	}

	/**
	 * Fetch a session by its public id.
	 *
	 * @param string $public_id 32 hex characters.
	 * @return array|null
	 */
	public static function get( $public_id ) {
		global $wpdb;

		if ( ! is_string( $public_id ) || ! preg_match( '/^[a-f0-9]{32}$/', $public_id ) ) {
			return null;
		}
		$table = self::table();
		$row   = $wpdb->get_row(
			$wpdb->prepare( "SELECT * FROM {$table} WHERE public_id = %s LIMIT 1", $public_id ),
			ARRAY_A
		);
		return is_array( $row ) ? $row : null;
	}

	/**
	 * Fetch the newest live session created for an NZDWorld token.
	 *
	 * @param string $token_hash SHA-256 of the NZDWorld token.
	 * @return array|null
	 */
	public static function get_by_token_hash( $token_hash ) {
		global $wpdb;

		$table = self::table();
		$row   = $wpdb->get_row(
			$wpdb->prepare(
				"SELECT * FROM {$table} WHERE token_hash = %s AND status = %s ORDER BY id DESC LIMIT 1",
				(string) $token_hash,
				self::STATUS_READY
			),
			ARRAY_A
		);
		return is_array( $row ) ? $row : null;
	}

	/**
	 * Mark a session as redeemed. Returns false when it was already redeemed.
	 *
	 * @param int $id Row id.
	 * @return bool
	 */
	public static function mark_redeemed( $id ) {
		global $wpdb;

		$table   = self::table();
		$updated = $wpdb->query(
			$wpdb->prepare(
				"UPDATE {$table} SET status = %s, redeemed_at = %d WHERE id = %d AND status = %s",
				self::STATUS_REDEEMED,
				time(),
				(int) $id,
				self::STATUS_READY
			)
		);
		return 1 === (int) $updated;
	}

	/**
	 * Mark a ready session as expired. Returns false when it was already handled,
	 * so statistics are only incremented once per session.
	 *
	 * @param int $id Row id.
	 * @return bool
	 */
	public static function mark_expired( $id ) {
		global $wpdb;

		$table   = self::table();
		$updated = $wpdb->query(
			$wpdb->prepare(
				"UPDATE {$table} SET status = %s WHERE id = %d AND status = %s",
				self::STATUS_EXPIRED,
				(int) $id,
				self::STATUS_READY
			)
		);
		return 1 === (int) $updated;
	}

	/**
	 * Mark a session as expired/failed.
	 *
	 * @param int    $id     Row id.
	 * @param string $status New status.
	 * @return void
	 */
	public static function mark_status( $id, $status ) {
		global $wpdb;

		$allowed = array( self::STATUS_EXPIRED, self::STATUS_FAILED, self::STATUS_REDEEMED );
		if ( ! in_array( $status, $allowed, true ) ) {
			return;
		}
		$table = self::table();
		$wpdb->update(
			$table,
			array( 'status' => $status ),
			array( 'id' => (int) $id ),
			array( '%s' ),
			array( '%d' )
		);
	}

	/**
	 * Update the last_seen timestamp (cheap usage signal).
	 *
	 * @param int $id Row id.
	 * @return void
	 */
	public static function touch( $id ) {
		global $wpdb;

		$table = self::table();
		$wpdb->update( $table, array( 'last_seen' => time() ), array( 'id' => (int) $id ), array( '%d' ), array( '%d' ) );
	}

	/**
	 * Delete abandoned sessions and expire stale ones.
	 *
	 * @return int Number of rows removed.
	 */
	public static function cleanup() {
		global $wpdb;

		$table = self::table();
		$now   = time();

		$wpdb->query(
			$wpdb->prepare(
				"UPDATE {$table} SET status = %s WHERE status = %s AND expires_at > 0 AND expires_at < %d",
				self::STATUS_EXPIRED,
				self::STATUS_READY,
				$now
			)
		);

		$removed = $wpdb->query(
			$wpdb->prepare(
				"DELETE FROM {$table} WHERE expires_at > 0 AND expires_at < %d AND status IN (%s, %s, %s)",
				$now - 3600,
				self::STATUS_EXPIRED,
				self::STATUS_FAILED,
				self::STATUS_REDEEMED
			)
		);

		return (int) $removed;
	}

	/**
	 * Count of sessions by status (used for the admin summary).
	 *
	 * @return array
	 */
	public static function counts() {
		global $wpdb;

		$table = self::table();
		$rows  = $wpdb->get_results(
			"SELECT status, COUNT(*) AS total FROM {$table} GROUP BY status",
			ARRAY_A
		);
		$out   = array(
			self::STATUS_READY    => 0,
			self::STATUS_REDEEMED => 0,
			self::STATUS_EXPIRED  => 0,
			self::STATUS_FAILED   => 0,
		);
		if ( is_array( $rows ) ) {
			foreach ( $rows as $row ) {
				if ( isset( $row['status'] ) ) {
					$out[ (string) $row['status'] ] = (int) $row['total'];
				}
			}
		}
		return $out;
	}

	/**
	 * Generate a cryptographically random public id.
	 *
	 * @return string 32 hex characters.
	 */
	public static function new_public_id() {
		return bin2hex( random_bytes( 16 ) );
	}

	/**
	 * Whether a session is still within its lifetime and not finished.
	 *
	 * @param array $session Session row.
	 * @return bool
	 */
	public static function is_live( $session ) {
		if ( ! is_array( $session ) || ! isset( $session['status'] ) ) {
			return false;
		}
		if ( self::STATUS_READY !== $session['status'] ) {
			return false;
		}
		return (int) $session['expires_at'] > time();
	}
}

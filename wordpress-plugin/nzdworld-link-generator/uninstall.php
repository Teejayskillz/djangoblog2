<?php
/**
 * Uninstall routine: remove every trace of the plugin.
 *
 * Kept separate so deactivating the plugin never destroys the configuration.
 *
 * @package NZDWorld_Link_Generator
 */

defined( 'WP_UNINSTALL_PLUGIN' ) || exit;

/**
 * Delete the plugin's options and tables for one site.
 *
 * @return void
 */
function nzd_lg_uninstall_site() {
	global $wpdb;

	delete_option( 'nzd_lg_settings' );
	delete_option( 'nzd_lg_stats' );
	delete_option( 'nzd_lg_version' );

	$table = $wpdb->prefix . 'nzd_generator_sessions';
	$wpdb->query( "DROP TABLE IF EXISTS {$table}" );

	$wpdb->query(
		"DELETE FROM {$wpdb->options} WHERE option_name LIKE '_transient_nzd_lg_%' OR option_name LIKE '_transient_timeout_nzd_lg_%'"
	);
}

if ( is_multisite() ) {
	$nzd_lg_sites = get_sites(
		array(
			'fields' => 'ids',
			'number' => 500,
		)
	);
	foreach ( $nzd_lg_sites as $nzd_lg_site_id ) {
		switch_to_blog( (int) $nzd_lg_site_id );
		nzd_lg_uninstall_site();
		restore_current_blog();
	}
} else {
	nzd_lg_uninstall_site();
}

wp_clear_scheduled_hook( 'nzd_lg_cleanup' );

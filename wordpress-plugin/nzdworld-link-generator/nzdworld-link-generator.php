<?php
/**
 * Plugin Name:       NZDWorld Link Generator
 * Plugin URI:        https://nzdworld.com/
 * Description:       Receives secure download handoffs from NZDWorld, shows a countdown generator popup on a random published job article, and continues the download after the countdown.
 * Version:           1.0.0
 * Requires at least: 5.8
 * Requires PHP:      7.4
 * Author:            NZDWorld
 * Author URI:        https://nzdworld.com/
 * License:           GPL-2.0-or-later
 * License URI:       https://www.gnu.org/licenses/gpl-2.0.html
 * Text Domain:       nzdworld-link-generator
 * Domain Path:       /languages
 *
 * This plugin is the WordPress half of the NZDWorld External Link Generator.
 * It never receives, stores or displays a download destination: NZDWorld only
 * releases the real link server-to-server after the countdown has elapsed and
 * the one-time session has been redeemed.
 */

defined( 'ABSPATH' ) || exit;

define( 'NZD_LG_VERSION', '1.0.0' );
define( 'NZD_LG_FILE', __FILE__ );
define( 'NZD_LG_DIR', plugin_dir_path( __FILE__ ) );
define( 'NZD_LG_URL', plugin_dir_url( __FILE__ ) );
define( 'NZD_LG_OPTION', 'nzd_lg_settings' );
define( 'NZD_LG_STATS_OPTION', 'nzd_lg_stats' );
define( 'NZD_LG_CRON_HOOK', 'nzd_lg_cleanup' );
define( 'NZD_LG_ENTRY_SLUG', 'nzdworld-link' );

require_once NZD_LG_DIR . 'includes/class-nzd-lg-options.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-crypto.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-sessions.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-api-client.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-entry.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-frontend.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-admin.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-activator.php';
require_once NZD_LG_DIR . 'includes/class-nzd-lg-plugin.php';

register_activation_hook( __FILE__, array( 'NZD_LG_Activator', 'activate' ) );
register_deactivation_hook( __FILE__, array( 'NZD_LG_Activator', 'deactivate' ) );

add_action( 'plugins_loaded', array( 'NZD_LG_Plugin', 'init' ) );

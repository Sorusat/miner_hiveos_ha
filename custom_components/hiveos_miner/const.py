"""Constants for the HiveOS Miner integration."""

DOMAIN = "hiveos_miner"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_NAME = "name"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_USERNAME = "root"
DEFAULT_PASSWORD = "root"
DEFAULT_NAME = "Miner"
DEFAULT_SCAN_INTERVAL = 30

# CGI endpoints exposed by the HiveOS local web interface.
# All of them require HTTP digest authentication.
ENDPOINT_STATUS = "/cgi-bin/get_miner_status.cgi"
ENDPOINT_START = "/cgi-bin/start_miner.cgi"
ENDPOINT_STOP = "/cgi-bin/stop_miner.cgi"
ENDPOINT_RESUME = "/cgi-bin/resume_miner.cgi"

# Miner states derived from the status JSON.
STATE_MINING = "mining"
STATE_STARTING = "starting"
STATE_SUSPENDED = "suspended"
STATE_STOPPED = "stopped"
STATE_UNKNOWN = "unknown"

STATE_LABELS = {
    STATE_MINING: "Майнинг",
    STATE_STARTING: "Запуск...",
    STATE_SUSPENDED: "Приостановлен",
    STATE_STOPPED: "Остановлен",
    STATE_UNKNOWN: "Неизвестно",
}

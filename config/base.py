# -*- coding: utf-8 -*-

# Global server name
server_name = 'a cuwo mitm server'

# Max number of players on the server at once
max_players = 8

# Seed for the server
seed = 6283529

# Time speed modifier, 1.0 is default
time_modifier = 1.0

# List of scripts to run on the server on startup.
# Consider turning on 'pvp', i.e. player versus player
scripts = ['log', 'ddos', 'commands', 'welcome', 'console', 'master',
           'anticheat']

# Ranks used for rights management. Keys are ranks, and values are
# a list of user types under that rank. Right now, only 'admin' is defined,
# but scripts can restrict their usage depending on the user type
ranks = {
    'default': ['member'],
    'admin': ['member','admin']
}

# Used by the welcome.py script. Sends a small welcome message to users,
# replacing %(server_name)s with the server name defined in this file.
welcome = ["Welcome to %(server_name)s!",
           "A cuwo mitm server with changes by Jakky89",
           "Type /help to get a list of commands."]

# Logging variables
log_name = './logs/log.txt'
rotate_daily = True

# Profile file. Set to something other than None to enable.
profile_file = None

# Max connections per IP to prevent DoS.
max_connections_per_ip = 5

# Connection timeout time in seconds
connection_timeout = 10.0

# Network interface to bind to. Leave empty for all IPv4 interfaces.
network_interface = ''

# MITM ip address and port of server running original Cube World server software
mitm_ip = '127.0.0.1'
mitm_port = 12345

# Server port. Do not change this unless you have a modified client!
port = 12345

# Server send rate. Change this to a lower value for high-traffic servers.
# The vanilla server uses 50, but 20 or 25 may be more sensible.
update_fps = 20

# Turns on world simulation and enables terrain generation (depends on
# cuwo.tgen). This may not be needed for barebones PvP servers.
# (work-in-progress feature, turned off by default)
use_world = False

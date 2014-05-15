# Copyright (c) Mathias Kaerlev 2013-2014.
#
# This file is part of cuwo.
#
# cuwo is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# cuwo is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with cuwo.  If not, see <http://www.gnu.org/licenses/>.

"""
Default set of commands bundled with cuwo
"""

from cuwo.script import ServerScript, command, admin
from cuwo.common import get_chunk
from cuwo.constants import CLASS_NAMES, CLASS_SPECIALIZATIONS
from cuwo.packet import HitPacket, HIT_NORMAL
from cuwo.vector import Vector3
from cuwo import database
from cuwo import common
import platform


class CommandServer(ServerScript):
    pass


def get_class():
    return CommandServer


@command
def help(script, name=None):
    """Returns information about commands."""
    if name is None:
        commands = [item.name for item in script.get_commands()]
        commands.sort()
        return 'Commands: ' + ', '.join(commands)
    else:
        command = script.get_command(name)
        if command is None:
            return 'No such command'
        return command.get_help()


@command
def register(script, password=None, repeating=None):
    if not password or not repeating:
        return '[INFO] Use /register <Password> <Password Repeating> to register in order to get your own unique numeric ID.'
    if password != repeating:
        return '[REGISTRATION] Your password does not equal its repeating.'
    regid = database.register_player(script.server.db_con, script.connection.name, script.connection.address.host, password)
    if regid is not None:
        database.update_player(script.server.db_con, regid, script.connection.name)
        return '[REGISTRATION] You can use /login %s %s now everytime you want to login.' % (regid, password)
    return '[ERROR] Registration failed.'


@command
def login(script, id, password):
    if not id or not password:
        return '[INFO] Use /login <ID> <Password> when you are already registered else use /register <Password> <Password Repeating> to register in order to get your own unique numeric ID.'
    try:
        id = int(id)
    except Exception:
        return '[ERROR] Invalid ID given.'
    dbres = database.login_player(script.server.db_con, id, password)
    if dbres:
        script.connection.login_id = id
        database.update_player(script.server.db_con, id, script.connection.name)
        if not dbres[1] is None:
            script.connection.rank = dbres[1].lower()
            user_types = script.server.ranks.get(script.connection.rank, [])
            script.connection.rights.update(user_types)
            return '[LOGIN] Successfully logged in as %s %s. Your last login name was %s with IP %s.' % (script.connection.rank, script.connection.name, dbres[0], dbres[2])
        return '[LOGIN] Successfully logged in as %s. Your last login name was %s with IP %s.' % (script.connection.name, dbres[0], dbres[2])
    else:
        return '[ERROR] Got no database result!'
    return '[ERROR] Login failed.'


@command
def logout(script):
    if not script.connection.login_id:
        return '[INFO] You are not logged in!'
    database.update_player(script.server.db_con, script.connection.login_id, script.connection.name)
    script.connection.login_id = None
    script.connection.rank = None
    script.connection.rights.update([])
    return 'Successfully logged out.'


@command
@admin
def kick(script, name):
    """Kicks the specified player."""
    player = script.get_player(name)
    player.kick()


@command
def whereis(script, name=None):
    """Shows where a player is in the world."""
    player = script.get_player(name)
    if player is script.connection:
        message = 'You are at %s'
    else:
        message = '%s is at %%s' % player.name
    return message % (get_chunk(player.position),)


@command
def pm(script, name, *message):
    """Sends a private message to a player."""
    player = script.get_player(name)
    message = ' '.join(message)
    player.send_chat('%s (PM): %s' % (script.connection.name, message))
    return 'PM sent'


@command
def tell(script, name=None, *args):
    """Sends a private message to a player."""
    if not name:
        return '[INFO] Command to tell something to a specific player: /tell <player> <message>'
    try:
        player = script.get_player(name)
        if not player:
            return '[ERROR] Could not find player with that name!'
        if player is script.connection:
            return '[ERROR] You can not tell messages back to yourself!'
        message = '%s -> %s: %s' % (script.connection.name, player.name, ' '.join(args))
        player.send_chat(message)
        return message
    except:
        pass
    return '[EXCEPTION] Could not tell message to %s!' % player.name


@command
def spawn(script):
    """Sends player to global spawn point."""
    gsp = database.load_data(script.server.db_con, 'globalspawnpoint', [550301073408, 550301073408, 1000000])
    script.connection.teleport(*gsp)
    return 'Sent to spawn.'


@command
@admin
def setspawn(script):
    """Sets global spawn point."""
    if database.save_data(script.server.db_con, 'globalspawnpoint', [script.connection.position.x,script.connection.position.y,script.connection.position.z]) is True:
        return 'Global spawn point set.'
    return 'Could not set global spawn point!'


@command
def warp(script, warp_name=None):
    """Sends player to warp point."""
    if warp_name is None:
        wp = database.get_warps(script.server.db_con)
        if len(wp) > 0:
            msg = 'Warps: '
            msg += ', '.join(wp)
            return msg
        else:
            return 'No warps defined.'
    else:
        wp = database.get_warp(script.server.db_con, warp_name)
        if wp is not None:
            script.connection.teleport(*wp)
            return 'Sent to warp point "%s".' % warp_name
        return 'Could not get a warp point with such name.'


@command
@admin
def setwarp(script, warp_name):
    """Sets a warp point."""
    if database.set_warp(script.server.db_con, warp_name, script.connection.position.x, script.connection.position.y, script.connection.position.z) is True:
        return 'Warp point "%s" defined.' % warp_name
    return 'Could not define warp point!'


@command
@admin
def delwarp(script, warp_name):
    """Deletes a warp point."""
    if database.del_warp(script.server.db_con, warp_name) is True:
        return 'Warp point "%s" deleted.' % warp_name
    return 'Could not delete warp point!'


@command
@admin
def heal(script, name=None, hp=None):
    """Heals a player by a specified amount."""
    if name is None:
        script.connection.heal(hp)
        return 'You healed yourself.'
    else:
        player = script.get_player(name)
        player.heal(hp)
        return 'You healed %s' % player.name


def who_where(script, include_where):
    server = script.server
    player_count = len(server.players)
    if player_count == 0:
        return 'No players connected'
    formatted_names = []
    for player in server.players.values():
        name = '%s #%s' % (player.name, player.entity_id)
        if include_where:
            name += ' %s' % (get_chunk(player.position),)
        formatted_names.append(name)
    noun = 'player' if player_count == 1 else 'players'
    msg = '%s %s connected: ' % (player_count, noun)
    msg += ', '.join(formatted_names)
    return msg


@command
def who(script):
    """Lists players."""
    return who_where(script, False)


@command
def whowhere(script):
    """Lists players and their locations."""
    return who_where(script, True)


@command
def player(script, name):
    """Returns information about a player."""
    player = script.get_player(name)
    entity = player.entity_data
    typ = entity.class_type
    klass = CLASS_NAMES[typ]
    spec = CLASS_SPECIALIZATIONS[typ][entity.specialization]
    level = entity.level
    return "'%s' is a lvl %s %s (%s)" % (player.name, level, klass, spec)


@command
@admin
def setrank(script, id, rank):
    if not id or not rank:
        return '[INFO] Use /setrank <ID> <Rank> to set the rank of user with the given id.'
    rank = rank.lower()
    if rank not in script.server.ranks:
        return '[ERROR] Invalid rank!'
    ret = database.set_player_rank(script.server.db_con, id, rank)
    if ret is True:
        user_types = script.server.ranks.get(rank, [])
        for player in script.server.players.values():
            if (player.login_id is not None) and (player.login_id == id):
                player.rights.update(user_types)
                print '[INFO] Rights of %s updated.' % player.name
        return '[SUCCESS] Rank of player with id %s set to %s' % (id, rank)
    return '[RANK] Could not set rank!'


@command
@admin
def load(script, name):
    """Loads a script at runtime."""
    name = str(name)
    if name in script.server.scripts:
        return 'Script %r already loaded' % name
    script.server.load_script(name)
    return 'Script %r loaded' % name


@command
@admin
def unload(script, name):
    """Unloads a script at runtime."""
    name = str(name)
    if not script.server.unload_script(name):
        return 'Script %r is not loaded' % name
    return 'Script %r unloaded' % name


@command
def scripts(script):
    """Lists the currently loaded scripts."""
    return 'Scripts: ' + ', '.join(script.server.scripts.items)

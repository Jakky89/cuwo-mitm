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

from cuwo.twistedreactor import install_reactor
install_reactor()
from twisted.internet.protocol import Factory, Protocol
from twisted.internet import reactor
from twisted.internet.endpoints import TCP4ClientEndpoint
from twisted.internet.task import LoopingCall

from cuwo.packet import (PacketHandler, write_packet, CS_PACKETS, SC_PACKETS,
                         ClientVersion, JoinPacket, SeedData, EntityUpdate,
                         ClientChatMessage, ServerChatMessage,
                         create_entity_data, UpdateFinished,
                         ServerUpdate, ServerFull, ServerMismatch,
                         INTERACT_DROP, INTERACT_PICKUP, ChunkItemData,
                         ChunkItems, InteractPacket, PickupAction,
                         HitPacket, ShootPacket)
from cuwo.types import IDPool, MultikeyDict, AttributeSet
from cuwo import constants
from cuwo.common import (get_clock_string, parse_clock, parse_command,
                         get_chunk, get_distance_2d, get_distance_3d, filter_string)
from cuwo.script import ScriptManager
from cuwo.config import ConfigObject
from cuwo import database
from cuwo import entity

import collections
import os
import sys
import pprint
import traceback

# initialize packet instances for sending
join_packet = JoinPacket()
seed_packet = SeedData()
chat_packet = ServerChatMessage()
entity_packet = EntityUpdate()
update_finished_packet = UpdateFinished()
mismatch_packet = ServerMismatch()
server_full_packet = ServerFull()


class RelayClient(Protocol):
    def __init__(self, protocol):
        self.protocol = protocol

    def dataReceived(self, data):
        self.protocol.serverDataReceived(data)


class RelayFactory(Factory):
    def __init__(self, protocol):
        self.protocol = protocol

    def buildProtocol(self, addr):
        return RelayClient(self.protocol)


class CubeWorldConnection(Protocol):
    """
    Protocol used for players
    """
    relay_client = None
    relay_packets = None
    has_joined = False
    entity_id = None
    entity_data = None
    login_id = None
    rank = None
    disconnected = False
    scripts = None
    chunk = None

    old_pos = None
    old_health = None
    old_level = None
    old_xp = None


    def __init__(self, server, addr):
        self.address = addr
        self.server = server

        self.relay_packets = []

    # connection methods

    def got_relay_client(self, p):
        self.relay_client = p
        for data in self.relay_packets:
            self.relay_client.transport.write(data)
        self.relay_packets = None
        print 'Relaying Client Packets.'

    def connectionMade(self):
        self.transport.setTcpNoDelay(True)

        server = self.server

        self.client_packet_handler = PacketHandler(CS_PACKETS,
                                                   self.on_client_packet)
        self.server_packet_handler = PacketHandler(SC_PACKETS,
                                                   self.on_server_packet)

        server.connections.add(self)
        self.rights = AttributeSet()

        self.scripts = ScriptManager()
        server.scripts.call('on_new_connection', connection=self)

        point = TCP4ClientEndpoint(reactor, self.server.config.base.mitm_ip, self.server.config.base.mitm_port)
        d = point.connect(RelayFactory(self))
        d.addCallback(self.got_relay_client)

    def serverDataReceived(self, data):
        self.server_packet_handler.feed(data)

    def dataReceived(self, data):
        self.client_packet_handler.feed(data)

    def disconnect(self, reason=None):
        self.transport.loseConnection()
        self.connectionLost(reason)

    def connectionLost(self, reason):
        if self.relay_client is not None:
            self.relay_client.transport.loseConnection()
        if self.disconnected:
            return
        self.disconnected = True
        if self.login_id is not None:
            database.update_online_seconds(self.server.db_con, self.login_id)
        self.server.connections.discard(self)
        if self.has_joined:
            del self.server.players[self]
            print '[INFO] Player %s #%s left the game.' % (self.name, self.entity_id)
            self.server.send_chat('<<< %s #%s left the game' % (self.name, self.entity_id))
        if self.entity_data is not None:
            del self.server.entities[self.entity_id]
        if self.scripts is not None:
            self.scripts.unload()

    # packet methods

    def send_packet(self, packet):
        self.transport.write(write_packet(packet))

    def relay_packet(self, packet):
        if self.relay_client is None:
            self.relay_packets.append(write_packet(packet))
        else:
            self.relay_client.transport.write(write_packet(packet))

    def on_server_packet(self, packet):
        if packet.packet_id == EntityUpdate.packet_id:
            if packet.entity_id == self.entity_id:
                self.on_entity_packet(packet)
        elif packet.packet_id == JoinPacket.packet_id:
            self.entity_id = packet.entity_id
        self.send_packet(packet)

    def on_client_packet(self, packet):
        if self.disconnected:
            return
        if packet is None:
            print 'Invalid packet received'
            self.disconnect()
            raise StopIteration()
        if packet.packet_id == EntityUpdate.packet_id:
            if self.on_entity_packet(packet) is True:
                self.relay_packet(packet)
        elif packet.packet_id == ClientChatMessage.packet_id:
            self.on_chat_packet(packet)
        elif packet.packet_id == InteractPacket.packet_id:
            self.on_interact_packet(packet)
        elif packet.packet_id == HitPacket.packet_id:
            self.on_hit_packet(packet)
        elif packet.packet_id == ShootPacket.packet_id:
            self.on_shoot_packet(packet)
        else:
            self.relay_packet(packet)

    def on_entity_packet(self, packet):
        if self.entity_id is None:
            return True

        if self.entity_data is None:
            self.entity_data = create_entity_data()
            self.server.entities[self.entity_id] = self.entity_data

        mask = packet.update_entity(self.entity_data)
        self.entity_data.mask |= mask
        if not self.has_joined and getattr(self.entity_data, 'name', None):
            self.on_join()
            return True

        result = True
        self.scripts.call('on_entity_update', mask=mask)
        # XXX clean this up
        if entity.is_pos_set(mask):
            if self.on_pos_update() is False:
                result = False
        if entity.is_mode_set(mask):
            self.scripts.call('on_mode_update')
        if entity.is_class_set(mask):
            self.scripts.call('on_class_update')
        if entity.is_name_set(mask):
            self.scripts.call('on_name_update')
        if entity.is_multiplier_set(mask):
            self.scripts.call('on_multiplier_update')
        if entity.is_level_set(mask):
            self.scripts.call('on_level_update')
        if entity.is_equipment_set(mask):
            self.scripts.call('on_equipment_update')
        if entity.is_skill_set(mask):
            self.scripts.call('on_skill_update')
        if entity.is_appearance_set(mask):
            self.scripts.call('on_appearance_update')
        if entity.is_charged_mp_set(mask):
            self.scripts.call('on_charged_mp_update')
        if entity.is_flags_set(mask):
            self.scripts.call('on_flags_update')
        if entity.is_consumable_set(mask):
            self.scripts.call('on_consumable_update')
        return result

    def on_pos_update(self):
        chunk = get_chunk(self.position)
        if self.chunk is None:
            self.chunk = chunk
        elif chunk != self.chunk:
            # Distance check
            if (abs(chunk[0]-self.chunk[0]) > 1) or (abs(chunk[1]-self.chunk[1]) > 1):
                self.disconnect('[ANTICHEAT] Traveled distance to large')
                print '[ANTICHEAT] Traveled distance of %s was to large' % self.name
                return False
            if abs(chunk[0]) < 2 or abs(chunk[1]) < 2:
                self.disconnect('[ANTICHEAT] Out of world border')
                self.teleport(550301073408, 550301073408, 1000000)
                print '[ANTICHEAT] %s was out of world border' % self.name
                return False
            self.chunk = chunk
        self.scripts.call('on_pos_update')
        return True

    def on_chat_packet(self, packet):
        message = filter_string(packet.value).strip()
        if not message:
            return
        message = self.on_chat(message)
        if not message:
            return
        chat_packet.entity_id = self.entity_id
        chat_packet.value = message
        self.server.broadcast_packet(chat_packet)
        print '[CHAT] %s: %s' % (self.name, message)

    def on_interact_packet(self, packet):
        interact_type = packet.interact_type
        item = packet.item_data
        if interact_type == INTERACT_DROP:
            pos = self.position.copy()
            if self.scripts.call('on_drop', item=item,
                                 pos=pos).result is False:
                return
        elif interact_type == INTERACT_PICKUP:
            pos = self.position.copy()
            if self.scripts.call('on_pickup', item=item,
                                 pos=pos).result is False:
                return
        self.relay_packet(packet)

    def on_hit_packet(self, packet):
        self.relay_packet(packet)

        try:
            target = self.server.entities[packet.target_id]
        except KeyError:
            return

        if self.scripts.call('on_hit',
                             target=target,
                             packet=packet).result is False:
            return

        #self.server.update_packet.player_hits.append(packet)
        if target.hp <= 0:
            return
        target.hp -= packet.damage
        if target.hp <= 0:
            self.scripts.call('on_kill', target=target)

    def on_shoot_packet(self, packet):
        self.relay_packet(packet)

    # handlers

    def on_join(self):
        if self.scripts.call('on_join').result is False:
            return False

        print '[INFO] Player %s joined the game at %s' % (self.name, self.position)
        self.server.send_chat('>>> %s #%s joined the game' % (self.name, self.entity_id))
        self.server.players[(self.entity_id,)] = self
        self.has_joined = True
        return True

    def on_command(self, command, parameters):
        self.scripts.call('on_command', command=command, args=parameters)
        if ((not parameters) or (command == 'register') or (command == 'login')):
            print '[COMMAND] %s: /%s' % (self.name, command)
        else:
            print '[COMMAND] %s: /%s %s' % (self.name, command, ' '.join(parameters))

    def on_chat(self, message):
        if message.startswith('/'):
            command, args = parse_command(message[1:])
            self.on_command(command, args)
            return
        event = self.scripts.call('on_chat', message=message)
        if event.result is False:
            return
        return event.message

    # other methods

    def send_chat(self, value):
        packet = ServerChatMessage()
        packet.entity_id = 0
        packet.value = value
        self.send_packet(packet)

    def give_item(self, item):
        action = PickupAction()
        action.entity_id = self.entity_id
        action.item_data = item
        self.server.update_packet.pickups.append(action)

    def send_lines(self, lines):
        current_time = 0
        for line in lines:
            reactor.callLater(current_time, self.send_chat, line)
            current_time += 2

    def heal(self, amount=None):
        if amount is not None and amount <= 0:
            return False

        packet = EntityUpdate()

        if amount is None or amount + self.entity_data.hp > 1000:
            self.entity_data.hp = 1000
        else:
            self.entity_data.hp += amount

        packet.set_entity(self.entity_data, self.entity_id)
        self.relay_packet(packet)

        packet.set_entity(self.entity_data, 0)
        self.send_packet(packet)

    def kick(self):
        self.send_chat('You have been kicked')
        self.disconnect()
        self.server.send_chat('[INFO] %s has been kicked' % self.name)

    def teleport(self, to_x, to_y, to_z):
        packet = EntityUpdate()

        self.entity_data.pos.x = to_x
        self.entity_data.pos.y = to_y
        self.entity_data.pos.z = to_z

        self.chunk = get_chunk(self.entity_data.pos)
        self.old_pos = self.entity_data.pos

        packet.set_entity(self.entity_data, 0)
        self.send_packet(packet)

        packet.set_entity(self.entity_data, self.entity_id)
        self.relay_packet(packet)

    # convienience methods

    @property
    def position(self):
        if self.entity_data is None:
            return None
        return self.entity_data.pos

    @property
    def name(self):
        if self.entity_data is None:
            return None
        return self.entity_data.name


class BanProtocol(Protocol):
    """
    Protocol used for banned players.
    Ignores data from client and only sends JoinPacket/ServerChatMessage
    """

    def __init__(self, message=None):
        self.message = message

    def send_packet(self, packet):
        self.transport.write(write_packet(packet))

    def connectionMade(self):
        join_packet.entity_id = 1
        self.send_packet(join_packet)
        self.disconnect_call = reactor.callLater(0.1, self.disconnect)

    def disconnect(self):
        if self.message is not None:
            chat_packet.entity_id = 0
            chat_packet.value = self.message
        self.send_packet(chat_packet)
        self.transport.loseConnection()

    def connectionLost(self, reason):
        if self.disconnect_call.active():
            self.disconnect_call.cancel()


class CubeWorldServer(Factory):
    items_changed = False
    exit_code = None
    world = None

    def __init__(self, config):
        self.config = config
        base = config.base

        # game-related
        self.update_packet = ServerUpdate()
        self.update_packet.reset()

        self.connections = set()
        self.players = MultikeyDict()

        self.entities = {}

        # DATABASE
        self.db_con = database.get_connection()
        database.create_structure(self.db_con)

        self.update_loop = LoopingCall(self.update)
        self.update_loop.start(1.0 / base.update_fps, False)

        # server-related
        self.git_rev = base.get('git_rev', None)

        self.ranks = {}
        for k, v in base.ranks.iteritems():
            self.ranks[k.lower()] = v

        self.scripts = ScriptManager()
        for script in base.scripts:
            self.load_script(script)

        # time
        self.start_time = reactor.seconds()
        self.next_secondly_check = self.start_time+1

        # start listening
        self.listen_tcp(base.port, self)

    def buildProtocol(self, addr):
        # return None here to refuse the connection.
        # will use this later to hardban e.g. DoS
        ret = self.scripts.call('on_connection_attempt', address=addr).result
        if ret is False:
            print '[WARNING] Connection attempt for %s blocked by script!' % addr.host
            return None
        elif ret is not None:
            return BanProtocol(ret)
        if database.is_banned_ip(self.db_con, addr.host):
            print '[INFO] Banned client %s tried to join.' % addr.host
            return BanProtocol('You are banned on this server.')
        return CubeWorldConnection(self, addr)

    def update(self):
        self.scripts.call('update')

        # entity updates
        for entity_id, entity in self.entities.iteritems():
            entity.mask = 0

    def send_chat(self, value):
        packet = ServerChatMessage()
        packet.entity_id = 0
        packet.value = value
        self.broadcast_packet(packet)

    def broadcast_packet(self, packet):
        data = write_packet(packet)
        for player in self.players.values():
            player.transport.write(data)

    # line/string formatting options based on config

    def format(self, value):
        format_dict = {'server_name': self.config.base.server_name}
        return value % format_dict

    def format_lines(self, value):
        lines = []
        for line in value:
            lines.append(self.format(line))
        return lines

    # script methods

    def load_script(self, name):
        try:
            return self.scripts[name]
        except KeyError:
            pass
        try:
            mod = __import__('scripts.%s' % name, globals(), locals(), [name])
        except ImportError, e:
            traceback.print_exc(e)
            return None
        script = mod.get_class()(self)
        print 'Loaded script %r' % name
        return script

    def unload_script(self, name):
        try:
            self.scripts[name].unload()
        except KeyError:
            return False
        print 'Unloaded script %r' % name
        return True

    def call_command(self, user, command, args):
        """
        Calls a command from an external interface, e.g. IRC, console
        """
        return self.scripts.call('on_command', user=user, command=command,
                                 args=args).result

    def get_mode(self):
        return self.scripts.call('get_mode').result

    # command convenience methods (for /help)

    def get_commands(self):
        for script in self.scripts.get():
            if script.commands is None:
                continue
            for command in script.commands.itervalues():
                yield command

    def get_command(self, name):
        for script in self.scripts.get():
            if script.commands is None:
                continue
            command = script.commands.get(name, None)
            if command:
                return command

    # data store methods

    #def load_data(self, name, default=None):
    #    path = './%s.dat' % name
    #    try:
    #        with open(path, 'rU') as fp:
    #            data = fp.read()
    #    except IOError:
    #        return default
    #    return eval(data)

    #def save_data(self, name, value):
    #    path = './%s.dat' % name
    #    data = pprint.pformat(value, width=1)
    #    with open(path, 'w') as fp:
    #        fp.write(data)

    def load_data(self, name, default=None):
        return database.load_data(self.db_con, name, default)

    def save_data(self, name, value):
        return database.save_data(self.db_con, name, value)

    # stop/restart

    def stop(self, code=None):
        self.exit_code = code
        reactor.stop()

    # twisted wrappers

    def listen_udp(self, *arg, **kw):
        interface = self.config.base.network_interface
        return reactor.listenUDP(*arg, interface=interface, **kw)

    def listen_tcp(self, *arg, **kw):
        interface = self.config.base.network_interface
        return reactor.listenTCP(*arg, interface=interface, **kw)

    def connect_tcp(self, *arg, **kw):
        interface = self.config.base.network_interface
        return reactor.connectTCP(*arg, bindAddress=(interface, 0), **kw)


def main():
    # for py2exe
    if hasattr(sys, 'frozen'):
        path = os.path.dirname(unicode(sys.executable,
                                       sys.getfilesystemencoding()))
        root = os.path.abspath(os.path.join(path, '..'))
        sys.path.append(root)

    config = ConfigObject('./config')
    server = CubeWorldServer(config)

    print 'cuwo running on port %s' % config.base.port

    if config.base.profile_file is None:
        reactor.run()
    else:
        import cProfile
        cProfile.run('reactor.run()', filename=config.base.profile_file)

    database.close_connection(server.db_con)

    sys.exit(server.exit_code)

if __name__ == '__main__':
    main()

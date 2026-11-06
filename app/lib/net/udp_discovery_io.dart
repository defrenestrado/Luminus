import 'dart:async';
import 'dart:convert';
import 'dart:io';

const int _discoveryPort = 5112;

Future<String?> discoverViaUdp() async {
  RawDatagramSocket? socket;
  StreamSubscription<RawSocketEvent>? sub;
  try {
    socket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
    socket.broadcastEnabled = true;
    final completer = Completer<String?>();
    sub = socket.listen((event) {
      if (event != RawSocketEvent.read) return;
      final datagram = socket?.receive();
      if (datagram == null) return;
      final text = utf8.decode(datagram.data, allowMalformed: true).trim();
      if (!text.startsWith('LUMINUS')) return;
      final parts = text.split(':');
      final port = parts.length > 1 ? int.tryParse(parts[1].trim()) : null;
      if (!completer.isCompleted) {
        completer.complete(
          'http://${datagram.address.address}:${port ?? 5111}',
        );
      }
    });
    final payload = utf8.encode('LUMINUS_DISCOVER');
    final targets = <InternetAddress>{InternetAddress('255.255.255.255')};
    for (final iface in await NetworkInterface.list(
      type: InternetAddressType.IPv4,
    )) {
      for (final addr in iface.addresses) {
        final octets = addr.address.split('.');
        if (octets.length != 4) continue;
        targets.add(
          InternetAddress('${octets[0]}.${octets[1]}.${octets[2]}.255'),
        );
      }
    }
    for (final target in targets) {
      try {
        socket.send(payload, target, _discoveryPort);
      } catch (_) {}
    }
    return await completer.future.timeout(
      const Duration(milliseconds: 1500),
      onTimeout: () => null,
    );
  } catch (_) {
    return null;
  } finally {
    await sub?.cancel();
    socket?.close();
  }
}

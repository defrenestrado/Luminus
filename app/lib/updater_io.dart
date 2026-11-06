import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;

import 'api.dart';

class AppUpdater {
  static const int buildNumber = int.fromEnvironment(
    'LUMINUS_BUILD_NUMBER',
    defaultValue: 0,
  );
  static const MethodChannel _channel =
      MethodChannel('com.luminus.local.luminus/update');

  static Future<Map<String, dynamic>?> check() async {
    try {
      final r = await http
          .get(
            ApiClient.apkUri('build.json'),
            headers: const {'Cache-Control': 'no-cache'},
          )
          .timeout(const Duration(seconds: 8));
      if (r.statusCode != 200) return null;
      return jsonDecode(r.body) as Map<String, dynamic>;
    } catch (_) {
      return null;
    }
  }

  static Future<void> downloadAndInstall({
    void Function(double progress)? onProgress,
    void Function(String status)? onStatus,
  }) async {
    final dir = await _channel.invokeMethod<String>('cacheDir');
    if (dir == null || dir.isEmpty) {
      throw 'Pasta temporária indisponível.';
    }
    final file = File('$dir/luminus-update.apk');
    if (await file.exists()) await file.delete();
    final client = http.Client();
    IOSink? sink;
    try {
      onStatus?.call('Baixando APK...');
      final request = http.Request('GET', ApiClient.apkUri('luminus.apk'));
      final response =
          await client.send(request).timeout(const Duration(seconds: 20));
      if (response.statusCode != 200) {
        throw 'Servidor respondeu HTTP ${response.statusCode}.';
      }
      final total = response.contentLength ?? 0;
      sink = file.openWrite();
      var received = 0;
      await for (final chunk
          in response.stream.timeout(const Duration(seconds: 60))) {
        sink.add(chunk);
        received += chunk.length;
        if (total > 0) {
          onProgress?.call((received / total).clamp(0.0, 1.0));
        }
      }
      await sink.close();
      sink = null;
      if (!await file.exists() || await file.length() < 1024) {
        throw 'Download incompleto.';
      }
      onStatus?.call('Abrindo instalador...');
      await _channel.invokeMethod('installApk', {'path': file.path});
      onStatus?.call('Instalador aberto. Confirme a instalação.');
    } finally {
      try {
        await sink?.close();
      } catch (_) {}
      client.close();
    }
  }
}

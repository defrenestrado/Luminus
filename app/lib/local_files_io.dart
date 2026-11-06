import 'dart:io';

import 'package:flutter/services.dart';

class LocalFiles {
  static const MethodChannel _channel =
      MethodChannel('com.luminus.local.luminus/update');

  static String _safeName(String name) {
    final clean = name.replaceAll(RegExp(r'[^A-Za-z0-9._-]'), '_');
    return clean.isEmpty ? 'luminus-world-pack.json' : clean;
  }

  static Future<String?> _export(
    String content, {
    required String suggestedName,
    required String mime,
  }) async {
    final dir = await _channel.invokeMethod<String>('cacheDir');
    if (dir == null || dir.isEmpty) {
      throw 'Pasta temporária indisponível.';
    }
    final name = _safeName(suggestedName);
    final file = File('$dir/$name');
    await file.writeAsString(content, flush: true);
    return _channel.invokeMethod<String>(
      'exportFile',
      {'path': file.path, 'name': suggestedName, 'mime': mime},
    );
  }

  /// Grava o conteúdo no cache e abre o seletor de destino do Android.
  /// Retorna a URI escolhida ou null se o usuário cancelar.
  static Future<String?> exportJson(
    String content, {
    required String suggestedName,
  }) =>
      _export(
        content,
        suggestedName: suggestedName,
        mime: 'application/json',
      );

  static Future<String?> exportText(
    String content, {
    required String suggestedName,
    String mime = 'text/plain',
  }) =>
      _export(content, suggestedName: suggestedName, mime: mime);

  /// Abre o seletor de arquivos do Android e devolve o texto escolhido.
  /// Retorna null se o usuário cancelar.
  static Future<String?> pickJson() async {
    final path = await _channel.invokeMethod<String>('pickJson');
    if (path == null || path.isEmpty) return null;
    return File(path).readAsString();
  }
}

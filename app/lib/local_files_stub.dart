class LocalFiles {
  static Future<String?> exportJson(
    String content, {
    required String suggestedName,
  }) async {
    throw 'Exportar World Pack só está disponível no app Android.';
  }

  static Future<String?> exportText(
    String content, {
    required String suggestedName,
    String mime = 'text/plain',
  }) async {
    throw 'Exportar crônica só está disponível no app Android.';
  }

  static Future<String?> pickJson() async {
    throw 'Importar World Pack só está disponível no app Android.';
  }
}

class DeepLink {
  DeepLink._();

  static int? chatId(String route) {
    final raw = route.trim();
    if (raw.isEmpty) return null;
    final uri = Uri.tryParse(raw);
    final path = (uri != null && uri.path.isNotEmpty) ? uri.path : raw;
    final match = RegExp(r'/chats/(\d+)').firstMatch(path);
    if (match != null) return int.tryParse(match.group(1)!);
    final query = uri?.queryParameters['chat'];
    return query == null ? null : int.tryParse(query);
  }
}

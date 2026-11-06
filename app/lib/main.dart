import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'deeplink.dart';
import 'screens/bootstrap_screen.dart';
import 'theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  ApiClient.adminKey = prefs.getString('admin_key') ?? '';
  if (kIsWeb) {
    final saved = prefs.getString('server_url');
    final host = Uri.base.host;
    ApiClient.baseUrl = saved ??
        (host.isEmpty ? ApiClient.defaultServer : 'http://$host:5111');
  } else {
    ApiClient.baseUrl =
        prefs.getString('server_url') ?? ApiClient.defaultServer;
    final url = await ApiClient.discover();
    if (url != prefs.getString('server_url')) {
      await prefs.setString('server_url', url);
    }
  }
  final route = WidgetsBinding.instance.platformDispatcher.defaultRouteName;
  runApp(LuminusApp(initialChatId: DeepLink.chatId(route)));
}

class LuminusApp extends StatelessWidget {
  final int? initialChatId;
  const LuminusApp({super.key, this.initialChatId});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Luminus',
      debugShowCheckedModeBanner: false,
      theme: buildLuminusTheme(),
      builder: (context, child) {
        final content = child ?? const SizedBox.shrink();
        if (!kIsWeb) return content;
        return LuminusBackground(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1100),
              child: content,
            ),
          ),
        );
      },
      home: BootstrapScreen(initialChatId: initialChatId),
    );
  }
}

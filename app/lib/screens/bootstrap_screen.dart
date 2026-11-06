import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import '../updater.dart';
import 'chat_screen.dart';
import 'home_shell.dart';
import 'settings_screen.dart';

class BootstrapScreen extends StatefulWidget {
  final int? initialChatId;
  const BootstrapScreen({super.key, this.initialChatId});

  @override
  State<BootstrapScreen> createState() => _BootstrapScreenState();
}

class _BootstrapScreenState extends State<BootstrapScreen> {
  String _server = 'connecting';
  String _main = 'idle';
  String _utility = 'idle';
  String _error = '';
  bool _updateAvailable = false;
  String _updateLabel = '';
  bool _updating = false;
  double _updateProgress = 0;
  bool _navigated = false;
  bool _showSkip = false;
  Timer? _skipTimer;

  @override
  void initState() {
    super.initState();
    _skipTimer = Timer(const Duration(seconds: 6), () {
      if (mounted) setState(() => _showSkip = true);
    });
    _boot();
  }

  @override
  void dispose() {
    _skipTimer?.cancel();
    super.dispose();
  }

  Future<void> _boot() async {
    if (_navigated) return;
    setState(() {
      _server = 'connecting';
      _main = 'idle';
      _utility = 'idle';
      _error = '';
    });
    try {
      await ApiClient.health();
      _server = 'online';
    } catch (_) {
      await ApiClient.discover();
      try {
        await ApiClient.health();
        _server = 'online';
      } catch (_) {
        _server = 'offline';
      }
    }
    if (!mounted) return;
    setState(() {});
    if (_server == 'offline') {
      setState(() {
        _error = 'Servidor offline. Confira o Wi-Fi e o endereço em Ajustes.';
      });
      return;
    }
    _checkUpdate();
    ApiClient.warmupStart()
        .then<void>((state) => _applyWarmup(state))
        .catchError((Object _) {});
    _enter();
  }

  Future<void> _checkUpdate() async {
    final info = await AppUpdater.check();
    if (!mounted || info == null) return;
    final serverBuild = (info['build_number'] as num?)?.toInt() ?? 0;
    if (AppUpdater.buildNumber > 0 && serverBuild > AppUpdater.buildNumber) {
      final at = DateTime.tryParse((info['built_at'] ?? '').toString());
      setState(() {
        _updateAvailable = true;
        _updateLabel = at == null
            ? 'Nova versão disponível.'
            : 'Nova versão de ${at.toLocal()}';
      });
    }
  }

  void _applyWarmup(Map<String, dynamic> state) {
    if (!mounted) return;
    setState(() {
      _main = (state['main'] ?? 'idle').toString();
      _utility = (state['utility'] ?? 'idle').toString();
      final error = (state['error'] ?? '').toString();
      if (error.isNotEmpty && _main != 'fallback') _error = error;
    });
  }

  void _enter() {
    if (_navigated || !mounted) return;
    _navigated = true;
    _skipTimer?.cancel();
    final navigator = Navigator.of(context);
    navigator.pushReplacement(
      PageRouteBuilder(
        pageBuilder: (_, _, _) => const HomeShell(),
        transitionsBuilder: (_, animation, _, child) =>
            FadeTransition(opacity: animation, child: child),
        transitionDuration: const Duration(milliseconds: 350),
      ),
    );
    final chatId = widget.initialChatId;
    if (chatId != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _openDeepLink(navigator, chatId);
      });
    }
  }

  Future<void> _openDeepLink(NavigatorState navigator, int chatId) async {
    try {
      final session = await ApiClient.session(chatId);
      final character = await ApiClient.character(session.characterId);
      await navigator.push(
        MaterialPageRoute(
          builder: (_) => ChatScreen(character: character, session: session),
        ),
      );
    } catch (_) {}
  }

  Future<void> _update() async {
    if (_updating) return;
    setState(() {
      _updating = true;
      _updateProgress = 0;
    });
    try {
      await AppUpdater.downloadAndInstall(
        onProgress: (p) {
          if (mounted) setState(() => _updateProgress = p);
        },
        onStatus: (s) {
          if (mounted) setState(() => _updateLabel = s);
        },
      );
    } catch (e) {
      if (mounted) setState(() => _updateLabel = 'Falhou: $e');
    } finally {
      if (mounted) setState(() => _updating = false);
    }
  }

  Future<void> _openSettings() async {
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const SettingsScreen()),
    );
    if (mounted) _boot();
  }

  String _serverLabel() {
    switch (_server) {
      case 'online':
        return 'Conectado';
      case 'connecting':
        return 'Conectando...';
      default:
        return 'Offline';
    }
  }

  String _stateLabel(String state) {
    switch (state) {
      case 'ready':
        return 'Pronto';
      case 'loading':
        return 'Carregando...';
      case 'fallback':
        return 'Modelo reserva';
      case 'error':
        return 'Falhou';
      default:
        return 'Aguardando';
    }
  }

  Widget _stageRow(String title, String state, String label) {
    IconData icon = Icons.radio_button_unchecked;
    Color color = kMuted;
    if (state == 'ready' || state == 'online' || state == 'fallback') {
      icon = Icons.check_circle;
      color = kAccent;
    } else if (state == 'error' || state == 'offline') {
      icon = Icons.error_outline;
      color = kDanger;
    } else if (state == 'connecting') {
      color = kPrimary;
    } else if (state == 'loading') {
      color = kPrimary;
    }
    final Widget leading =
        (state == 'loading' || state == 'connecting')
            ? const SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : Icon(icon, size: 20, color: color);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: kBorder),
      ),
      child: Row(
        children: [
          leading,
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              title,
              style: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600),
            ),
          ),
          Text(
            label,
            style: TextStyle(
              color: color,
              fontSize: 11.5,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }

  Widget _updateCard() {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: kAccent.withValues(alpha: .08),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: kAccent.withValues(alpha: .35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Row(
            children: [
              Icon(Icons.system_update_alt, color: kAccent, size: 20),
              SizedBox(width: 8),
              Expanded(
                child: Text(
                  'Atualização do app',
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            _updateLabel,
            style: const TextStyle(color: kMuted, fontSize: 12, height: 1.4),
          ),
          const SizedBox(height: 10),
          if (_updating) ...[
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: LinearProgressIndicator(
                value: _updateProgress > 0 ? _updateProgress : null,
                minHeight: 6,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              '${(_updateProgress * 100).toStringAsFixed(0)}%',
              textAlign: TextAlign.center,
              style: const TextStyle(color: kMuted, fontSize: 11),
            ),
          ] else
            Row(
              children: [
                Expanded(
                  child: FilledButton(
                    onPressed: _update,
                    child: const Text('Atualizar agora'),
                  ),
                ),
                const SizedBox(width: 8),
                TextButton(
                  onPressed: () => setState(() => _updateAvailable = false),
                  child: const Text('Depois'),
                ),
              ],
            ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final ready =
        (_main == 'ready' || _main == 'fallback') && _utility == 'ready';
    return LuminusBackground(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        body: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(28, 32, 28, 32),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const _SplashOrb(),
                  const SizedBox(height: 18),
                  const Text(
                    'Luminus',
                    style: TextStyle(
                      fontSize: 30,
                      fontWeight: FontWeight.w800,
                      letterSpacing: .5,
                    ),
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'Preparando o mundo',
                    style: TextStyle(color: kMuted, fontSize: 13),
                  ),
                  const SizedBox(height: 28),
                  _stageRow('Servidor', _server, _serverLabel()),
                  const SizedBox(height: 12),
                  _stageRow('Narrador 27B', _main, _stateLabel(_main)),
                  const SizedBox(height: 12),
                  _stageRow('Assistente 9B', _utility, _stateLabel(_utility)),
                  const SizedBox(height: 22),
                  if (!ready)
                    ClipRRect(
                      borderRadius: BorderRadius.circular(8),
                      child: const LinearProgressIndicator(minHeight: 5),
                    ),
                  const SizedBox(height: 12),
                  Text(
                    _error.isNotEmpty
                        ? _error
                        : ready
                            ? 'Tudo pronto. Entrando...'
                            : 'A primeira carga do narrador pode levar até 2 minutos.',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: _error.isNotEmpty ? kDanger : kMuted,
                      fontSize: 12,
                      height: 1.5,
                    ),
                  ),
                  if (_updateAvailable) ...[
                    const SizedBox(height: 22),
                    _updateCard(),
                  ],
                  if (_server == 'offline' || _error.isNotEmpty) ...[
                    const SizedBox(height: 18),
                    Row(
                      children: [
                        Expanded(
                          child: FilledButton.icon(
                            onPressed: _boot,
                            icon: const Icon(Icons.refresh),
                            label: const Text('Tentar de novo'),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: _openSettings,
                            icon: const Icon(Icons.tune),
                            label: const Text('Ajustes'),
                          ),
                        ),
                      ],
                    ),
                  ],
                  if (_showSkip && !ready) ...[
                    const SizedBox(height: 10),
                    TextButton(
                      onPressed: _enter,
                      child: const Text('Entrar sem esperar'),
                    ),
                  ],
                  const SizedBox(height: 10),
                  Text(
                    'Servidor: ${ApiClient.baseUrl}',
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: kMuted, fontSize: 10.5),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _SplashOrb extends StatefulWidget {
  const _SplashOrb();

  @override
  State<_SplashOrb> createState() => _SplashOrbState();
}

class _SplashOrbState extends State<_SplashOrb>
    with SingleTickerProviderStateMixin {
  late final AnimationController _spin = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 12),
  )..repeat();

  @override
  void dispose() {
    _spin.dispose();
    super.dispose();
  }

  Widget _ember(int index, double t) {
    final phase = (t + index / 7) % 1.0;
    final angle = index * math.pi / 3.5 + t * math.pi * 2;
    final radius = 38 + phase * 30;
    final dx = math.cos(angle) * radius;
    final dy = math.sin(angle) * radius - phase * 34;
    final opacity = ((1 - phase) * 0.85).clamp(0.0, 0.85);
    return Transform.translate(
      offset: Offset(dx, dy),
      child: Opacity(
        opacity: opacity,
        child: Container(
          width: 4,
          height: 4,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: kAccent,
            boxShadow: [
              BoxShadow(color: kAccent.withValues(alpha: .85), blurRadius: 7),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 158,
      height: 158,
      child: AnimatedBuilder(
        animation: _spin,
        builder: (_, _) {
          final t = _spin.value;
          return Stack(
            alignment: Alignment.center,
            children: [
              Container(
                width: 150,
                height: 150,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: RadialGradient(
                    colors: [
                      kPrimary.withValues(alpha: .30),
                      kPrimary.withValues(alpha: .06),
                      Colors.transparent,
                    ],
                    stops: const [0, .55, 1],
                  ),
                ),
              ),
              Transform.rotate(
                angle: t * math.pi * 2,
                child: CustomPaint(
                  size: const Size(148, 148),
                  painter: const _MagicCirclePainter(),
                ),
              ),
              Transform.rotate(
                angle: -t * math.pi * 2 * 1.7,
                child: CustomPaint(
                  size: const Size(108, 108),
                  painter: const _MagicCirclePainter(inner: true),
                ),
              ),
              for (var i = 0; i < 7; i++) _ember(i, t),
              Transform.translate(
                offset: Offset(0, math.sin(t * math.pi * 4) * 4),
                child: Container(
                  width: 62,
                  height: 62,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: const LinearGradient(
                      colors: [kPrimaryDeep, kPrimary],
                    ),
                    boxShadow: [
                      BoxShadow(
                        color: kPrimary.withValues(alpha: .45),
                        blurRadius: 26,
                      ),
                    ],
                  ),
                  child: const Icon(
                    Icons.auto_stories,
                    size: 32,
                    color: Colors.white,
                  ),
                ),
              ),
            ],
          );
        },
      ),
    );
  }
}

class _MagicCirclePainter extends CustomPainter {
  final bool inner;
  const _MagicCirclePainter({this.inner = false});

  @override
  void paint(Canvas canvas, Size size) {
    final center = size.center(Offset.zero);
    final radius = size.width / 2;
    final stroke = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.2
      ..color = kAccent.withValues(alpha: .55);
    canvas.drawCircle(center, radius - 1, stroke);
    if (inner) {
      final dot = Paint()..color = kPrimary.withValues(alpha: .85);
      for (var i = 0; i < 8; i++) {
        final a = i * math.pi / 4;
        canvas.drawCircle(
          center + Offset(math.cos(a), math.sin(a)) * radius * 0.7,
          1.6,
          dot,
        );
      }
      return;
    }
    canvas.drawCircle(
      center,
      radius * 0.78,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = kPrimary.withValues(alpha: .45),
    );
    final dash = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1
      ..color = kPrimary.withValues(alpha: .35);
    for (var i = 0; i < 36; i += 2) {
      final a0 = i * math.pi / 18;
      final a1 = (i + 1) * math.pi / 18;
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius * 0.87),
        a0,
        a1 - a0,
        false,
        dash,
      );
    }
    final rune = Paint()..color = kAccent.withValues(alpha: .9);
    for (var i = 0; i < 12; i++) {
      final a = i * math.pi / 6;
      canvas.drawCircle(
        center + Offset(math.cos(a), math.sin(a)) * radius * 0.92,
        1.9,
        rune,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _MagicCirclePainter oldDelegate) =>
      oldDelegate.inner != inner;
}

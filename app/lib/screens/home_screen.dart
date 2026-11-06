import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import '../widgets/fx.dart';
import '../widgets/game_icon.dart';
import 'chat_screen.dart';

class HomeScreen extends StatefulWidget {
  final VoidCallback onExplore;
  const HomeScreen({super.key, required this.onExplore});

  @override
  State<HomeScreen> createState() => HomeScreenState();
}

class HomeScreenState extends State<HomeScreen> {
  late Future<_HomeData> _future;

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  void refresh() => setState(() => _future = _load());

  Future<_HomeData> _load() async {
    final characters = await ApiClient.characters(kind: 'npc');
    final sessions = await ApiClient.allSessions();
    return _HomeData(characters, sessions);
  }

  Future<void> _open(Character character, Session session) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ChatScreen(character: character, session: session),
      ),
    );
    refresh();
  }

  @override
  Widget build(BuildContext context) {
    return LuminusBackground(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: const [
              Text('Luminus',
                  style: TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w800,
                      letterSpacing: .4)),
              Text(
                'O mundo lembra de você',
                style: TextStyle(
                    color: kMuted,
                    fontSize: 11.5,
                    fontWeight: FontWeight.w400),
              ),
            ],
          ),
          actions: [
            IconButton(
              tooltip: 'Atualizar',
              icon: const Icon(Icons.refresh),
              onPressed: refresh,
            ),
          ],
        ),
        body: RefreshIndicator(
          onRefresh: () async => refresh(),
          child: FutureBuilder<_HomeData>(
            future: _future,
            builder: (context, snap) {
              if (snap.connectionState == ConnectionState.waiting) {
                return ListView(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                  children: [
                    const SkeletonBox(height: 172, radius: 26),
                    const SizedBox(height: 26),
                    const SkeletonBox(width: 140, height: 18, radius: 8),
                    const SizedBox(height: 14),
                    for (var i = 0; i < 4; i++)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: SkeletonBox(height: 70, radius: 20),
                      ),
                  ],
                );
              }
              if (snap.hasError) {
                return ListView(
                  padding: const EdgeInsets.fromLTRB(20, 48, 20, 100),
                  children: [
                    Icon(Icons.cloud_off,
                        size: 48, color: kPrimary.withValues(alpha: .8)),
                    const SizedBox(height: 14),
                    const Text('Não consegui falar com o servidor',
                        textAlign: TextAlign.center,
                        style:
                            TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 6),
                    Text(ApiClient.baseUrl,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: kMuted, fontSize: 12)),
                    const SizedBox(height: 18),
                    Center(
                      child: FilledButton.icon(
                        onPressed: refresh,
                        icon: const Icon(Icons.refresh),
                        label: const Text('Tentar de novo'),
                      ),
                    ),
                  ],
                );
              }
              final data = snap.data ?? _HomeData(const [], const []);
              final recent = data.recent;
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
                children: [
                  if (recent.isEmpty)
                    _startCard()
                  else
                    _continueCard(recent.first),
                  const SizedBox(height: 8),
                  if (recent.isNotEmpty) ...[
                    SectionHeader(
                      title: 'Recentes',
                      action: 'Personagens',
                      onAction: widget.onExplore,
                    ),
                    for (final item in recent.take(8)) _recentTile(item),
                  ],
                  if (recent.isEmpty) ...[
                    const SizedBox(height: 18),
                    OutlinedButton.icon(
                      onPressed: widget.onExplore,
                      icon: const Icon(Icons.groups_2_outlined),
                      label: const Text('Ver personagens'),
                    ),
                  ],
                ],
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _startCard() {
    return Container(
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(26),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF3A2A78), Color(0xFF1A1536)],
        ),
        border: Border.all(color: kBorder),
      ),
      child: Stack(
        children: [
          Positioned(
            right: -18,
            top: -22,
            child: GameIcon(
              'treasure-map',
              size: 136,
              color: Colors.white.withValues(alpha: .12),
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(Icons.auto_awesome, color: kAccent, size: 26),
              const SizedBox(height: 14),
              const Text(
                'Sua primeira história começa aqui',
                style: TextStyle(
                    fontSize: 19,
                    fontWeight: FontWeight.w800,
                    height: 1.25),
              ),
              const SizedBox(height: 6),
              const Text(
                'Escolha um NPC, defina quem você interpreta e deixe o mundo lembrar.',
                style: TextStyle(color: kMuted, fontSize: 12.5, height: 1.45),
              ),
              const SizedBox(height: 18),
              GlowPulse(
                child: FilledButton.icon(
                  onPressed: widget.onExplore,
                  icon: const Icon(Icons.play_arrow_rounded),
                  label: const Text('Começar agora'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _continueCard(_Recent item) {
    final avatar = item.session.sceneImage.trim().isNotEmpty
        ? item.session.sceneImage.trim()
        : item.character.avatar.trim();
    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(26),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF3A2A78), Color(0xFF1A1536)],
        ),
        border: Border.all(color: kBorder),
        image: avatar.isEmpty
            ? null
            : DecorationImage(
                image: NetworkImage(ApiClient.mediaUrl(avatar)),
                fit: BoxFit.cover,
                colorFilter: ColorFilter.mode(
                  Colors.black.withValues(alpha: .3),
                  BlendMode.darken,
                ),
                onError: (_, _) {},
              ),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(26),
          onTap: () => _open(item.character, item.session),
          child: Stack(
            children: [
              Positioned.fill(
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    borderRadius: BorderRadius.circular(26),
                    gradient: LinearGradient(
                      begin: Alignment.topLeft,
                      end: Alignment.bottomRight,
                      colors: [
                        const Color(0xFF3A2A78).withValues(alpha: .78),
                        const Color(0xFF1A1536).withValues(alpha: .94),
                      ],
                    ),
                  ),
                ),
              ),
              Positioned(
                right: -18,
                top: -24,
                child: GameIcon(
                  'spell-book',
                  size: 146,
                  color: Colors.white.withValues(alpha: .12),
                ),
              ),
              Padding(
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Icon(Icons.history_rounded,
                            size: 14, color: kAccent),
                        const SizedBox(width: 6),
                        const Text(
                          'CONTINUAR',
                          style: TextStyle(
                            color: kAccent,
                            fontSize: 10.5,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 1.4,
                          ),
                        ),
                        const Spacer(),
                        Text(
                          _when(item.session.updatedAt),
                          style: const TextStyle(
                              color: kMuted, fontSize: 11),
                        ),
                      ],
                    ),
                    const SizedBox(height: 14),
                    Row(
                      children: [
                        LuminusAvatar(
                          radius: 30,
                          url: item.character.avatar,
                          letter: item.character.name.isNotEmpty
                              ? item.character.name[0]
                              : '?',
                          ring: true,
                        ),
                        const SizedBox(width: 13),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                item.character.name,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                    fontSize: 17.5,
                                    fontWeight: FontWeight.w800),
                              ),
                              const SizedBox(height: 2),
                              Text(
                                item.session.title,
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                    color: kMuted,
                                    fontSize: 12.5,
                                    height: 1.35),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    Row(
                      children: [
                        _meta(
                          Icons.forum_outlined,
                          '${item.session.turnCount} '
                              '${item.session.turnCount == 1 ? 'turno' : 'turnos'}',
                        ),
                        const SizedBox(width: 8),
                        if (item.session.protagonistId != null)
                          _meta(Icons.person_outline, 'protagonista'),
                      ],
                    ),
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      child: FilledButton.icon(
                        onPressed: () => _open(item.character, item.session),
                        icon: const Icon(Icons.play_arrow_rounded, size: 20),
                        label: const Text('Retomar história'),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _meta(IconData icon, String text) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: .18),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: kBorder),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 13, color: kMuted),
          const SizedBox(width: 5),
          Text(text,
              style: const TextStyle(
                  color: kMuted,
                  fontSize: 10.5,
                  fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }

  Widget _recentTile(_Recent item) {
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: kBorder),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(20),
          onTap: () => _open(item.character, item.session),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                LuminusAvatar(
                  radius: 22,
                  url: item.character.avatar,
                  letter: item.character.name.isNotEmpty
                      ? item.character.name[0]
                      : '?',
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        item.session.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontSize: 14.5, fontWeight: FontWeight.w700),
                      ),
                      if (item.session.lastLine.trim().isNotEmpty) ...[
                        const SizedBox(height: 2),
                        Text(
                          item.session.lastLine,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: kText.withValues(alpha: .78),
                            fontSize: 11.5,
                          ),
                        ),
                      ],
                      const SizedBox(height: 3),
                      Text(
                        '${item.character.name} • ${_when(item.session.updatedAt)}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            color: kMuted, fontSize: 11.5),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                const Icon(Icons.play_arrow_rounded, color: kPrimary, size: 22),
              ],
            ),
          ),
        ),
      ),
    );
  }

  String _when(double ts) {
    if (ts <= 0) return '';
    final d = DateTime.fromMillisecondsSinceEpoch((ts * 1000).round());
    final now = DateTime.now();
    final diff = now.difference(d);
    final hm =
        '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';
    if (diff.inMinutes < 1) return 'agora';
    if (diff.inMinutes < 60) return 'há ${diff.inMinutes} min';
    if (d.year == now.year && d.month == now.month && d.day == now.day) {
      return 'hoje $hm';
    }
    final yesterday = now.subtract(const Duration(days: 1));
    if (d.year == yesterday.year &&
        d.month == yesterday.month &&
        d.day == yesterday.day) {
      return 'ontem $hm';
    }
    return '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')} $hm';
  }
}

class _HomeData {
  final List<Character> characters;
  final List<Session> sessions;
  final Map<int, Character> _byId;

  _HomeData(this.characters, this.sessions)
      : _byId = {for (final c in characters) c.id: c};

  List<_Recent> get recent {
    final out = <_Recent>[];
    for (final s in sessions) {
      final c = _byId[s.characterId];
      if (c != null) out.add(_Recent(c, s));
    }
    return out;
  }
}

class _Recent {
  final Character character;
  final Session session;
  const _Recent(this.character, this.session);
}

import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';
import 'character_form_screen.dart';
import 'chat_screen.dart';

class SessionsScreen extends StatefulWidget {
  final Character character;
  const SessionsScreen({super.key, required this.character});

  @override
  State<SessionsScreen> createState() => _SessionsScreenState();
}

class _SessionsScreenState extends State<SessionsScreen> {
  late Future<List<Session>> _future;
  bool _showArchived = false;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  void _reload() => setState(
    () => _future = ApiClient.sessions(
      widget.character.id,
      includeArchived: true,
    ),
  );

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<int?> _pickProtagonist() async {
    List<Character> pros;
    try {
      pros = await ApiClient.characters(kind: 'protagonist');
    } catch (_) {
      _snack('Não consegui carregar os protagonistas. Confira a conexão.');
      return -1;
    }
    if (!mounted) return -1;
    if (pros.isEmpty) {
      final create = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
          backgroundColor: kSurface,
          title: const Text('Quem você interpreta?'),
          content: const Text(
            'Você ainda não criou um protagonista. Ele dá corpo e voz às suas ações na história.\n\n'
            'Você pode criar um agora ou apenas narrar, sem interpretar ninguém.',
            style: TextStyle(fontSize: 13, height: 1.45),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Só narrar'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Agora não'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Criar protagonista'),
            ),
          ],
        ),
      );
      if (!mounted) return -1;
      if (create == true) {
        final created = await Navigator.push<bool>(
          context,
          MaterialPageRoute(
            builder: (_) => const CharacterFormScreen(kind: 'protagonist'),
          ),
        );
        if (created != true) return -1;
        try {
          pros = await ApiClient.characters(kind: 'protagonist');
        } catch (_) {
          _snack('Não consegui carregar os protagonistas.');
          return -1;
        }
        if (pros.isEmpty) return -1;
      } else if (create == false) {
        return null;
      } else {
        return -1;
      }
    }
    if (!mounted) return -1;
    final picked = await showDialog<int>(
      context: context,
      builder: (_) => SimpleDialog(
        title: const Text('Quem você interpreta?'),
        backgroundColor: kSurface,
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(24, 0, 24, 8),
            child: Text(
              'Escolha o personagem que representa você nesta história.',
              style: TextStyle(color: kMuted, fontSize: 12.5, height: 1.4),
            ),
          ),
          for (final p in pros)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(context, p.id),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(
                  children: [
                    LuminusAvatar(
                      radius: 14,
                      url: p.avatar,
                      letter: p.name.isNotEmpty ? p.name[0] : '?',
                    ),
                    const SizedBox(width: 10),
                    Text(p.name),
                  ],
                ),
              ),
            ),
          SimpleDialogOption(
            onPressed: () => Navigator.pop(context, -2),
            child: const Padding(
              padding: EdgeInsets.symmetric(vertical: 6),
              child: Text(
                'Sem protagonista (só narrar)',
                style: TextStyle(color: kMuted),
              ),
            ),
          ),
        ],
      ),
    );
    if (picked == null) return -1;
    if (picked == -2) return null;
    return picked;
  }

  Future<List<int>?> _pickCast(Character main) async {
    List<Character> npcs;
    try {
      npcs = await ApiClient.characters(kind: 'npc');
    } catch (_) {
      return const [];
    }
    npcs = npcs
        .where(
          (c) =>
              c.id != main.id &&
              (main.worldId == null || c.worldId == main.worldId),
        )
        .toList();
    if (npcs.isEmpty || !mounted) return const [];
    final selected = <int>{};
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          backgroundColor: kSurface,
          title: const Text('Elenco (opcional)'),
          content: SizedBox(
            width: double.maxFinite,
            child: ListView(
              shrinkWrap: true,
              children: [
                const Padding(
                  padding: EdgeInsets.only(bottom: 8),
                  child: Text(
                    'Escolha até 4 NPCs para contracenar com o principal. '
                    'Cada um terá ficha, presença e NR próprios.',
                    style: TextStyle(
                      color: kMuted,
                      fontSize: 12.5,
                      height: 1.4,
                    ),
                  ),
                ),
                for (final c in npcs)
                  CheckboxListTile(
                    value: selected.contains(c.id),
                    onChanged: (v) {
                      setDialogState(() {
                        if (v == true) {
                          if (selected.length < 4) selected.add(c.id);
                        } else {
                          selected.remove(c.id);
                        }
                      });
                    },
                    title: Text(c.name),
                    subtitle: c.tagline.isEmpty
                        ? null
                        : Text(
                            c.tagline,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 11.5),
                          ),
                    secondary: LuminusAvatar(
                      radius: 14,
                      url: c.avatar,
                      letter: c.name.isNotEmpty ? c.name[0] : '?',
                    ),
                    controlAffinity: ListTileControlAffinity.trailing,
                    contentPadding: EdgeInsets.zero,
                  ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () {
                selected.clear();
                Navigator.pop(context, true);
              },
              child: const Text('Sem elenco'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Confirmar'),
            ),
          ],
        ),
      ),
    );
    if (ok != true) return null;
    return selected.toList();
  }

  Future<int?> _pickPlaceId() async {
    List<Place> places;
    try {
      places = await ApiClient.places(worldId: widget.character.worldId);
    } catch (_) {
      return 0;
    }
    if (!mounted) return null;
    if (places.isEmpty) return 0;
    final picked = await showDialog<int>(
      context: context,
      builder: (_) => SimpleDialog(
        backgroundColor: kSurface,
        title: const Text('Onde começa a história?'),
        children: [
          const Padding(
            padding: EdgeInsets.fromLTRB(24, 0, 24, 8),
            child: Text(
              'Escolha um ambiente próprio ou deixe a IA criar o lugar.',
              style: TextStyle(color: kMuted, fontSize: 12.5, height: 1.4),
            ),
          ),
          SimpleDialogOption(
            onPressed: () => Navigator.pop(context, 0),
            child: const Padding(
              padding: EdgeInsets.symmetric(vertical: 6),
              child: Text('Deixar a IA criar'),
            ),
          ),
          for (final place in places)
            SimpleDialogOption(
              onPressed: () => Navigator.pop(context, place.id),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      place.name,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    if (place.description.isNotEmpty)
                      Text(
                        place.description,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: kMuted,
                          fontSize: 11.5,
                          height: 1.35,
                        ),
                      ),
                  ],
                ),
              ),
            ),
        ],
      ),
    );
    return picked; // null = cancelou
  }

  Future<void> _newSession() async {
    final pid = await _pickProtagonist();
    if (pid == -1) return;
    final cast = await _pickCast(widget.character);
    if (cast == null) return;
    final placeId = await _pickPlaceId();
    if (placeId == null) return;
    final s = await ApiClient.createSession(
      widget.character.id,
      protagonistId: pid,
      placeId: placeId == 0 ? null : placeId,
      castIds: cast,
    );
    if (!mounted) return;
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ChatScreen(character: widget.character, session: s),
      ),
    );
    _reload();
  }

  Future<void> _open(Session s) async {
    await Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => ChatScreen(character: widget.character, session: s),
      ),
    );
    _reload();
  }

  Future<void> _delete(Session s) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Apagar história?'),
        content: Text(s.title),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kDanger),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Apagar'),
          ),
        ],
      ),
    );
    if (ok == true) {
      await ApiClient.deleteSession(s.id);
      _reload();
    }
  }

  Future<void> _archive(Session s) async {
    final arquivar = !s.archived;
    try {
      await ApiClient.archiveSession(s.id, arquivar);
      _snack(arquivar ? 'História arquivada.' : 'História desarquivada.');
      _reload();
    } catch (e) {
      _snack(e is String ? e : ApiClient.friendly(e));
    }
  }

  String _when(double ts) {
    if (ts <= 0) return '';
    final d = DateTime.fromMillisecondsSinceEpoch((ts * 1000).round());
    final now = DateTime.now();
    final hm =
        '${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}';
    if (d.year == now.year && d.month == now.month && d.day == now.day) {
      return 'hoje $hm';
    }
    return '${d.day.toString().padLeft(2, '0')}/${d.month.toString().padLeft(2, '0')} $hm';
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.character;
    return LuminusBackground(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          title: Row(
            children: [
              LuminusAvatar(
                radius: 16,
                url: c.avatar,
                letter: c.name.isNotEmpty ? c.name[0] : '?',
              ),
              const SizedBox(width: 10),
              Expanded(child: Text(c.name, overflow: TextOverflow.ellipsis)),
            ],
          ),
          actions: [
            IconButton(
              tooltip: _showArchived ? 'Ver ativas' : 'Ver arquivadas',
              icon: Icon(
                _showArchived ? Icons.inventory_2 : Icons.inventory_2_outlined,
                color: _showArchived ? kAccent : null,
              ),
              onPressed: () => setState(() => _showArchived = !_showArchived),
            ),
          ],
        ),
        floatingActionButton: FloatingActionButton.extended(
          onPressed: _newSession,
          backgroundColor: kPrimary,
          foregroundColor: Colors.white,
          icon: const Icon(Icons.add),
          label: const Text('Nova história'),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(18),
          ),
        ),
        body: FutureBuilder<List<Session>>(
          future: _future,
          builder: (context, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return ListView(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                children: [
                  const SkeletonBox(height: 122, radius: 24),
                  const SizedBox(height: 26),
                  const SkeletonBox(width: 160, height: 18, radius: 8),
                  const SizedBox(height: 14),
                  for (var i = 0; i < 4; i++)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: SkeletonBox(height: 84, radius: 22),
                    ),
                ],
              );
            }
            if (snap.hasError) {
              return ListView(
                padding: const EdgeInsets.fromLTRB(16, 40, 16, 100),
                children: [
                  const Icon(Icons.cloud_off, size: 44, color: kMuted),
                  const SizedBox(height: 12),
                  const Center(
                    child: Text('Não consegui carregar as histórias.'),
                  ),
                  const SizedBox(height: 16),
                  Center(
                    child: FilledButton(
                      onPressed: _reload,
                      child: const Text('Tentar de novo'),
                    ),
                  ),
                ],
              );
            }
            final all = snap.data ?? [];
            final activeCount = all.where((s) => !s.archived).length;
            final sessions = all
                .where((s) => s.archived == _showArchived)
                .toList();
            return RefreshIndicator(
              onRefresh: () async => _reload(),
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.only(bottom: 110),
                children: [
                  _header(c, activeCount, all.length - activeCount),
                  SectionHeader(
                    title: _showArchived ? 'Arquivadas' : 'Suas histórias',
                    action: _showArchived ? null : 'Nova',
                    onAction: _newSession,
                  ),
                  if (all.isEmpty) _empty(),
                  if (all.isNotEmpty && sessions.isEmpty)
                    _emptyArchived(all.length),
                  for (final s in sessions) _card(s),
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _header(Character c, int count, int archived) {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(24),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF3A2A78), Color(0xFF1A1536)],
        ),
        border: Border.all(color: kBorder),
      ),
      child: Row(
        children: [
          LuminusAvatar(
            radius: 36,
            url: c.avatar,
            letter: c.name.isNotEmpty ? c.name[0] : '?',
            ring: true,
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  c.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                if (c.tagline.isNotEmpty) ...[
                  const SizedBox(height: 3),
                  Text(
                    c.tagline,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: kMuted,
                      fontSize: 12,
                      height: 1.4,
                    ),
                  ),
                ],
                const SizedBox(height: 8),
                Text(
                  '$count ${count == 1 ? 'ativa' : 'ativas'}'
                  '${archived > 0 ? ' • $archived arquivada${archived == 1 ? '' : 's'}' : ''}',
                  style: const TextStyle(
                    color: kAccent,
                    fontSize: 11.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _cover(Session s) {
    final raw = s.sceneImage.trim().isNotEmpty
        ? s.sceneImage.trim()
        : widget.character.avatar.trim();
    final fallback = Container(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [kPrimaryDeep, kPrimary],
        ),
      ),
      child: const Icon(Icons.auto_stories, color: Colors.white, size: 22),
    );
    return ClipRRect(
      borderRadius: BorderRadius.circular(14),
      child: SizedBox(
        width: 46,
        height: 46,
        child: raw.isEmpty
            ? fallback
            : Image.network(
                ApiClient.mediaUrl(raw),
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) => fallback,
              ),
      ),
    );
  }

  Widget _card(Session s) {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 0, 16, 12),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: kBorder),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(22),
          onTap: () => _open(s),
          onLongPress: () => _delete(s),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Row(
              children: [
                _cover(s),
                const SizedBox(width: 14),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              s.title,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 15.5,
                                fontWeight: FontWeight.w700,
                                letterSpacing: .1,
                              ),
                            ),
                          ),
                          if (s.archived) ...[
                            const SizedBox(width: 6),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 6,
                                vertical: 2,
                              ),
                              decoration: BoxDecoration(
                                color: kSurface2,
                                borderRadius: BorderRadius.circular(6),
                                border: Border.all(color: kBorder),
                              ),
                              child: const Text(
                                'ARQUIVADA',
                                style: TextStyle(
                                  color: kMuted,
                                  fontSize: 8,
                                  fontWeight: FontWeight.w700,
                                  letterSpacing: .6,
                                ),
                              ),
                            ),
                          ],
                        ],
                      ),
                      if (s.lastLine.trim().isNotEmpty) ...[
                        const SizedBox(height: 3),
                        Text(
                          s.lastLine,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: kText.withValues(alpha: .82),
                            fontSize: 11.5,
                            height: 1.35,
                          ),
                        ),
                      ],
                      const SizedBox(height: 4),
                      Text(
                        '${s.turnCount} turnos'
                        '${s.protagonistId != null ? ' • com protagonista' : ' • só narração'}'
                        '${s.summary.isNotEmpty ? ' • com memória' : ''}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: kMuted, fontSize: 12),
                      ),
                      if (s.updatedAt > 0) ...[
                        const SizedBox(height: 3),
                        Text(
                          'atualizada ${_when(s.updatedAt)}',
                          style: const TextStyle(color: kMuted, fontSize: 11.5),
                        ),
                      ],
                    ],
                  ),
                ),
                PopupMenuButton<String>(
                  tooltip: 'Mais',
                  icon: const Icon(Icons.more_vert, color: kMuted, size: 20),
                  color: kSurface2,
                  onSelected: (v) {
                    if (v == 'open') _open(s);
                    if (v == 'archive') _archive(s);
                    if (v == 'delete') _delete(s);
                  },
                  itemBuilder: (_) => [
                    const PopupMenuItem(
                      value: 'open',
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(
                          Icons.play_arrow_rounded,
                          color: kPrimary,
                        ),
                        title: Text('Continuar'),
                      ),
                    ),
                    PopupMenuItem(
                      value: 'archive',
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(
                          s.archived
                              ? Icons.unarchive_outlined
                              : Icons.archive_outlined,
                        ),
                        title: Text(s.archived ? 'Desarquivar' : 'Arquivar'),
                      ),
                    ),
                    const PopupMenuItem(
                      value: 'delete',
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(Icons.delete_outline, color: kDanger),
                        title: Text('Apagar', style: TextStyle(color: kDanger)),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _emptyArchived(int total) {
    final arquivadas = _showArchived;
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 4, 16, 0),
      padding: const EdgeInsets.all(26),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: kBorder),
      ),
      child: Column(
        children: [
          Icon(
            arquivadas ? Icons.inventory_2_outlined : Icons.done_all,
            size: 42,
            color: kMuted,
          ),
          const SizedBox(height: 10),
          Text(
            arquivadas ? 'Nada arquivado' : 'Tudo organizado',
            style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(
            arquivadas
                ? 'Use o menu de uma história para guardá-la aqui.'
                : 'Você tem $total história${total == 1 ? '' : 's'} '
                      'arquivada${total == 1 ? '' : 's'}. '
                      'Elas continuam salvas e podem voltar quando quiser.',
            textAlign: TextAlign.center,
            style: const TextStyle(color: kMuted, fontSize: 12.5, height: 1.4),
          ),
          const SizedBox(height: 14),
          OutlinedButton.icon(
            onPressed: () => setState(() => _showArchived = !_showArchived),
            icon: Icon(
              arquivadas ? Icons.inbox_outlined : Icons.inventory_2_outlined,
            ),
            label: Text(arquivadas ? 'Ver ativas' : 'Ver arquivadas'),
          ),
        ],
      ),
    );
  }

  Widget _empty() {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 4, 16, 0),
      padding: const EdgeInsets.all(28),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: kBorder),
      ),
      child: Column(
        children: [
          const Icon(Icons.bookmark_border, size: 44, color: kPrimary),
          const SizedBox(height: 12),
          const Text(
            'Nenhuma história ainda',
            style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          const Text(
            'Comece uma nova e o mundo começa a lembrar.',
            textAlign: TextAlign.center,
            style: TextStyle(color: kMuted, fontSize: 12.5, height: 1.4),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: _newSession,
            icon: const Icon(Icons.play_arrow_rounded),
            label: const Text('Nova história'),
          ),
        ],
      ),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../theme.dart';
import '../widgets/game_icon.dart';
import 'character_form_screen.dart';
import 'sessions_screen.dart';
import 'settings_screen.dart';

class CharactersScreen extends StatelessWidget {
  const CharactersScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const _CharacterList(
      kind: 'npc',
      title: 'Luminus',
      addLabel: 'NPC',
      showProtagonists: true,
    );
  }
}

class ProtagonistsScreen extends StatelessWidget {
  const ProtagonistsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return const _CharacterList(
      kind: 'protagonist',
      title: 'Protagonistas',
      addLabel: 'protagonista',
      showProtagonists: false,
    );
  }
}

class _CharacterList extends StatefulWidget {
  final String kind;
  final String title;
  final String addLabel;
  final bool showProtagonists;

  const _CharacterList({
    required this.kind,
    required this.title,
    required this.addLabel,
    required this.showProtagonists,
  });

  @override
  State<_CharacterList> createState() => _CharacterListState();
}

class _CharacterListState extends State<_CharacterList> {
  late Future<List<Character>> _future;
  final _search = TextEditingController();
  String _query = '';
  List<World> _worlds = const [];
  int? _worldFilter;
  String? _tagFilter;
  bool _showArchived = false;

  bool get isNpc => widget.kind == 'npc';

  @override
  void initState() {
    super.initState();
    _reload();
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<List<Character>> _load() async {
    final chars = await ApiClient.characters(
      kind: widget.kind,
      includeArchived: true,
    );
    try {
      _worlds = await ApiClient.worlds();
    } catch (_) {
      _worlds = const [];
    }
    return chars;
  }

  void _reload() => setState(() => _future = _load());

  bool _matches(Character c, String query) {
    return c.name.toLowerCase().contains(query) ||
        c.tagline.toLowerCase().contains(query) ||
        c.tags.any((t) => t.toLowerCase().contains(query));
  }

  Future<void> _openForm() async {
    final created = await Navigator.push<bool>(
      context,
      MaterialPageRoute(builder: (_) => CharacterFormScreen(kind: widget.kind)),
    );
    if (created == true) _reload();
  }

  Future<void> _edit(Character c) async {
    final saved = await Navigator.push<bool>(
      context,
      MaterialPageRoute(
        builder: (_) => CharacterFormScreen(kind: widget.kind, existing: c),
      ),
    );
    if (saved == true) _reload();
  }

  Future<void> _openSessions(Character c) async {
    await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => SessionsScreen(character: c)),
    );
    _reload();
  }

  Future<void> _delete(Character c) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text('Apagar ${c.name}?'),
        content: const Text('Remove o personagem e todas as histórias salvas.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancelar')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kDanger),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Apagar'),
          ),
        ],
      ),
    );
    if (ok == true) {
      await ApiClient.deleteCharacter(c.id);
      _reload();
    }
  }

  Future<void> _archive(Character c) async {
    final arquivar = !c.archived;
    try {
      await ApiClient.archiveCharacter(c.id, arquivar);
      _snack(arquivar ? '${c.name} arquivado.' : '${c.name} desarquivado.');
      _reload();
    } catch (e) {
      _snack(e is String ? e : ApiClient.friendly(e));
    }
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(text)),
    );
  }

  Map<String, dynamic> _bodyFor(Character c, {String? avatar}) => {
        'kind': c.kind,
        'name': c.name,
        'avatar': avatar ?? c.avatar,
        'tagline': c.tagline,
        'description_public': c.descriptionPublic,
        'persona': c.persona,
        'greeting': c.greeting,
        'appearance': c.appearance,
        'personality': c.personality,
        'backstory': c.backstory,
        'speech_style': c.speechStyle,
        'likes': c.likes,
        'dislikes': c.dislikes,
        'fears': c.fears,
        'relationship': c.relationship,
        'secrets': c.secrets,
        'example_dialogue': c.exampleDialogue,
        'tags': c.tags,
        'world_id': c.worldId,
        'model': c.model,
        'voice': c.voice,
        'visual_prompt': c.visualPrompt,
        'art_seed': c.artSeed,
        'visual_style': c.visualStyle,
      };

  Future<void> _generateCoverFor(Character c) async {
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => AlertDialog(
        backgroundColor: kSurface,
        content: const Row(
          children: [
            SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.5),
            ),
            SizedBox(width: 14),
            Expanded(
              child: Text(
                'Gerando capa com IA...\nPode levar até 1 minuto.',
                style: TextStyle(fontSize: 13, height: 1.4),
              ),
            ),
          ],
        ),
      ),
    );
    try {
      String world = '';
      if (c.worldId != null) {
        try {
          for (final w in await ApiClient.worlds()) {
            if (w.id == c.worldId) {
              world = w.name;
              break;
            }
          }
        } catch (_) {}
      }
      final result = await ApiClient.generateCover(
        characterId: c.id,
        name: c.name,
        kind: c.kind,
        appearance: c.appearance,
        personality: c.personality,
        backstory: c.backstory,
        tags: c.tags,
        world: world,
      );
      final url = (result['url'] ?? '').toString();
      if (url.isEmpty) throw 'A API não retornou a imagem.';
      await ApiClient.updateCharacter(c.id, _bodyFor(c, avatar: url));
      if (mounted) Navigator.pop(context);
      _reload();
      final warning = (result['warning'] ?? '').toString();
      _snack(warning.isNotEmpty ? warning : 'Nova capa gerada.');
    } catch (e) {
      if (mounted) Navigator.pop(context);
      _snack(e is String ? e : ApiClient.friendly(e));
    }
  }

  Future<void> _generatePortraitsFor(Character c) async {
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => AlertDialog(
        backgroundColor: kSurface,
        content: const Row(
          children: [
            SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.5),
            ),
            SizedBox(width: 14),
            Expanded(
              child: Text(
                'Gerando 3 expressões...\nPode levar alguns minutos.',
                style: TextStyle(fontSize: 13, height: 1.4),
              ),
            ),
          ],
        ),
      ),
    );
    try {
      final result = await ApiClient.generatePortraits(c.id);
      if (mounted) Navigator.pop(context);
      _reload();
      final warning = (result['warning'] ?? '').toString();
      _snack(warning.isNotEmpty ? warning : 'Retratos gerados.');
    } catch (e) {
      if (mounted) Navigator.pop(context);
      _snack(e is String ? e : ApiClient.friendly(e));
    }
  }

  Future<void> _showIdentity(Character c) async {
    var fresh = c;
    try {
      fresh = await ApiClient.character(c.id);
    } catch (_) {}
    if (!mounted) return;
    final prompt = fresh.visualPrompt;
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: kSurface,
        title: const Row(
          children: [
            Icon(Icons.fingerprint, color: kAccent),
            SizedBox(width: 8),
            Expanded(child: Text('Identidade visual')),
          ],
        ),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                prompt.isEmpty
                    ? 'Ainda não definida. Ela nasce junto com os retratos '
                        'e trava rosto, cabelo, olhos, roupa e paleta.'
                    : prompt,
                style: const TextStyle(fontSize: 13.5, height: 1.5),
              ),
              if (fresh.artSeed != 0) ...[
                const SizedBox(height: 12),
                Text(
                  'Semente fixa: ${fresh.artSeed}',
                  style: TextStyle(
                    fontSize: 11.5,
                    color: kMuted,
                    letterSpacing: .3,
                  ),
                ),
              ],
            ],
          ),
        ),
        actions: [
          if (prompt.isNotEmpty)
            TextButton(
              onPressed: () {
                Clipboard.setData(ClipboardData(text: prompt));
                Navigator.pop(ctx);
                _snack('Identidade copiada.');
              },
              child: const Text('Copiar'),
            ),
          if (prompt.isNotEmpty)
            TextButton(
              onPressed: () {
                Navigator.pop(ctx);
                _newIdentity(c);
              },
              child: const Text('Regerar'),
            ),
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Fechar'),
          ),
        ],
      ),
    );
  }

  Future<void> _newIdentity(Character c) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: kSurface,
        title: const Text('Nova identidade visual?'),
        content: const Text(
          'Novos traços fixos serão sorteados e os retratos atuais serão '
          'apagados. A capa/avatar não muda; gere uma nova depois se quiser.',
          style: TextStyle(fontSize: 13.5, height: 1.5),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancelar'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Regerar'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => AlertDialog(
        backgroundColor: kSurface,
        content: const Row(
          children: [
            SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(strokeWidth: 2.5),
            ),
            SizedBox(width: 14),
            Expanded(
              child: Text(
                'Criando identidade visual...',
                style: TextStyle(fontSize: 13, height: 1.4),
              ),
            ),
          ],
        ),
      ),
    );
    try {
      final result = await ApiClient.regenerateVisual(c.id);
      if (mounted) Navigator.pop(context);
      _reload();
      final warning = (result['warning'] ?? '').toString();
      _snack(warning.isNotEmpty
          ? warning
          : 'Nova identidade criada. Gere os retratos novamente.');
    } catch (e) {
      if (mounted) Navigator.pop(context);
      _snack(e is String ? e : ApiClient.friendly(e));
    }
  }

  void _menu(Character c) {
    showModalBottomSheet(
      context: context,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 40,
              height: 4,
              margin: const EdgeInsets.symmetric(vertical: 12),
              decoration: BoxDecoration(
                color: kBorder,
                borderRadius: BorderRadius.circular(4),
              ),
            ),
            if (isNpc)
              ListTile(
                leading: const Icon(Icons.play_arrow_rounded, color: kPrimary),
                title: const Text('Nova história'),
                subtitle: const Text('começar um save novo'),
                onTap: () {
                  Navigator.pop(context);
                  _openSessions(c);
                },
              ),
            ListTile(
              leading: const Icon(Icons.edit_outlined),
              title: const Text('Editar'),
              subtitle: const Text('persona, voz, aparência...'),
              onTap: () {
                Navigator.pop(context);
                _edit(c);
              },
            ),
            ListTile(
              leading: Icon(
                c.archived ? Icons.unarchive_outlined : Icons.archive_outlined,
              ),
              title: Text(c.archived ? 'Desarquivar' : 'Arquivar'),
              subtitle: Text(
                c.archived
                    ? 'volta a aparecer na lista'
                    : 'esconde sem apagar as histórias',
              ),
              onTap: () {
                Navigator.pop(context);
                _archive(c);
              },
            ),
            ListTile(
              leading: const Icon(Icons.auto_awesome, color: kAccent),
              title: const Text('Gerar capa'),
              subtitle: const Text('usa a mesma identidade fixa'),
              onTap: () {
                Navigator.pop(context);
                _generateCoverFor(c);
              },
            ),
            ListTile(
              leading: const Icon(Icons.fingerprint, color: kAccent),
              title: const Text('Identidade visual'),
              subtitle: const Text('traços fixos usados nas imagens'),
              onTap: () {
                Navigator.pop(context);
                _showIdentity(c);
              },
            ),
            ListTile(
              leading: const Icon(Icons.face_retouching_natural, color: kAccent),
              title: const Text('Gerar retratos'),
              subtitle: const Text('3 expressões para a cena'),
              onTap: () {
                Navigator.pop(context);
                _generatePortraitsFor(c);
              },
            ),
            ListTile(
              leading: const Icon(Icons.delete_outline, color: kDanger),
              title: const Text('Apagar', style: TextStyle(color: kDanger)),
              onTap: () {
                Navigator.pop(context);
                _delete(c);
              },
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return LuminusBackground(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(widget.title,
                  style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800, letterSpacing: .4)),
              Text(
                isNpc ? 'Histórias que respiram' : 'Quem você interpreta',
                style: const TextStyle(color: kMuted, fontSize: 11.5, fontWeight: FontWeight.w400),
              ),
            ],
          ),
          actions: [
            IconButton(
              tooltip: _showArchived ? 'Ver ativos' : 'Ver arquivados',
              icon: Icon(
                _showArchived
                    ? Icons.inventory_2
                    : Icons.inventory_2_outlined,
                color: _showArchived ? kAccent : null,
              ),
              onPressed: () => setState(() {
                _showArchived = !_showArchived;
                _worldFilter = null;
                _tagFilter = null;
              }),
            ),
            if (widget.showProtagonists)
              IconButton(
                tooltip: 'Protagonistas',
                icon: const Icon(Icons.groups_2_outlined),
                onPressed: () => Navigator.push(
                  context,
                    MaterialPageRoute(builder: (_) => const ProtagonistsScreen()),
                ),
              ),
          ],
        ),
        floatingActionButton: FloatingActionButton.extended(
          onPressed: _openForm,
          backgroundColor: kPrimary,
          foregroundColor: Colors.white,
          icon: const Icon(Icons.add),
          label: Text('Novo ${widget.addLabel}'),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
        ),
        body: RefreshIndicator(
          onRefresh: () async => _reload(),
          child: FutureBuilder<List<Character>>(
            future: _future,
            builder: (context, snap) {
              if (snap.connectionState == ConnectionState.waiting) {
                return ListView(
                  padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                  children: [
                    const SkeletonBox(height: 158, radius: 24),
                    const SizedBox(height: 26),
                    const SkeletonBox(width: 150, height: 18, radius: 8),
                    const SizedBox(height: 14),
                    for (var i = 0; i < 3; i++)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 12),
                        child: SkeletonBox(height: 98, radius: 22),
                      ),
                  ],
                );
              }
              if (snap.hasError) {
                return ListView(
                  padding: const EdgeInsets.fromLTRB(16, 40, 16, 100),
                  children: [
                    Icon(Icons.cloud_off, size: 48, color: kPrimary.withValues(alpha: .8)),
                    const SizedBox(height: 14),
                    const Text('Não consegui falar com o servidor',
                        textAlign: TextAlign.center,
                        style: TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 6),
                    Text(ApiClient.baseUrl,
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: kMuted, fontSize: 12)),
                    const SizedBox(height: 18),
                    Center(
                      child: FilledButton.icon(
                        onPressed: () async {
                          await Navigator.push(context,
                              MaterialPageRoute(builder: (_) => const SettingsScreen()));
                          _reload();
                        },
                        icon: const Icon(Icons.tune),
                        label: const Text('Abrir ajustes'),
                      ),
                    ),
                  ],
                );
              }
              final all = snap.data ?? [];
              final activeCount = all.where((c) => !c.archived).length;
              final archivedCount = all.length - activeCount;
              final chars =
                  all.where((c) => c.archived == _showArchived).toList();
              final query = _query.trim().toLowerCase();
              final shown = chars.where((c) {
                if (query.isNotEmpty && !_matches(c, query)) return false;
                if (_worldFilter != null && c.worldId != _worldFilter) {
                  return false;
                }
                if (_tagFilter != null &&
                    !c.tags.any(
                      (t) => t.toLowerCase() == _tagFilter!.toLowerCase(),
                    )) {
                  return false;
                }
                return true;
              }).toList();
              final baseTitle =
                  isNpc ? 'Seus NPCs' : 'Seus protagonistas';
              final title = _showArchived ? 'Arquivados' : baseTitle;
              return ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.only(bottom: 110),
                children: [
                  _hero(activeCount),
                  if (chars.isNotEmpty) _searchBar(),
                  if (chars.isNotEmpty) _filterChips(chars),
                  SectionHeader(
                    title: '$title'
                        '${shown.length != chars.length ? ' (${shown.length})' : ''}',
                    action: _showArchived ? null : 'Novo',
                    onAction: _openForm,
                  ),
                  if (all.isEmpty) _empty(),
                  if (all.isNotEmpty && chars.isEmpty)
                    _archivedHint(archivedCount),
                  if (chars.isNotEmpty && shown.isEmpty) _noResults(),
                  if (shown.isNotEmpty) ..._grouped(shown),
                ],
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _filterChips(List<Character> chars) {
    final tags = <String, int>{};
    for (final c in chars) {
      for (final raw in c.tags) {
        final tag = raw.trim();
        if (tag.isEmpty) continue;
        tags[tag] = (tags[tag] ?? 0) + 1;
      }
    }
    final ranked = tags.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    final topTags = ranked.take(8).map((e) => e.key).toList();
    if (_worlds.isEmpty && topTags.isEmpty) return const SizedBox.shrink();
    return SizedBox(
      height: 46,
      child: ListView(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
        children: [
          _chip(
            'Todos',
            selected: _worldFilter == null && _tagFilter == null,
            onTap: () => setState(() {
              _worldFilter = null;
              _tagFilter = null;
            }),
          ),
          for (final w in _worlds)
            _chip(
              w.name,
              selected: _worldFilter == w.id,
              onTap: () => setState(() {
                _worldFilter = _worldFilter == w.id ? null : w.id;
                _tagFilter = null;
              }),
            ),
          for (final tag in topTags)
            _chip(
              '#$tag',
              selected: _tagFilter?.toLowerCase() == tag.toLowerCase(),
              onTap: () => setState(() {
                final active = _tagFilter?.toLowerCase() == tag.toLowerCase();
                _tagFilter = active ? null : tag;
                _worldFilter = null;
              }),
            ),
        ],
      ),
    );
  }

  List<Widget> _grouped(List<Character> shown) {
    final byWorld = <int?, List<Character>>{};
    for (final c in shown) {
      byWorld.putIfAbsent(c.worldId, () => []).add(c);
    }
    final keys = <int?>[];
    for (final w in _worlds) {
      if (byWorld.containsKey(w.id)) keys.add(w.id);
    }
    for (final key in byWorld.keys) {
      if (!keys.contains(key)) keys.add(key);
    }
    final out = <Widget>[];
    for (final key in keys) {
      final list = byWorld[key]!;
      out.add(_worldHeader(key, list.length));
      out.add(
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: GridView.builder(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            gridDelegate: const SliverGridDelegateWithMaxCrossAxisExtent(
              maxCrossAxisExtent: 210,
              mainAxisSpacing: 12,
              crossAxisSpacing: 12,
              childAspectRatio: .69,
            ),
            itemCount: list.length,
            itemBuilder: (_, i) => _card(list[i]),
          ),
        ),
      );
    }
    return out;
  }

  String _worldName(int? id) {
    if (id == null) return 'Sem mundo';
    for (final w in _worlds) {
      if (w.id == id) return w.name;
    }
    return 'Mundo #$id';
  }

  Widget _worldHeader(int? id, int count) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 8),
      child: Row(
        children: [
          Icon(
            id == null ? Icons.blur_on : Icons.public,
            size: 15,
            color: kPrimary,
          ),
          const SizedBox(width: 7),
          Expanded(
            child: Text(
              _worldName(id),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                fontSize: 13.5,
                fontWeight: FontWeight.w700,
                letterSpacing: .2,
              ),
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(
              color: kSurface,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: kBorder),
            ),
            child: Text(
              '$count',
              style: const TextStyle(
                color: kMuted,
                fontSize: 10.5,
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _archivedHint(int archivedCount) {
    final arquivados = _showArchived;
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 4, 16, 0),
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: kBorder),
      ),
      child: Column(
        children: [
          Icon(
            arquivados ? Icons.inventory_2_outlined : Icons.auto_stories,
            size: 40,
            color: kMuted,
          ),
          const SizedBox(height: 10),
          Text(
            arquivados ? 'Nada arquivado' : 'Nenhum personagem ativo',
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(
            arquivados
                ? 'Use o menu de um personagem para guardá-lo aqui.'
                : 'Você tem $archivedCount arquivado'
                    '${archivedCount == 1 ? '' : 's'}. '
                    'Toque no ícone da caixa para vê-los.',
            textAlign: TextAlign.center,
            style: const TextStyle(color: kMuted, fontSize: 12.5, height: 1.4),
          ),
        ],
      ),
    );
  }

  Widget _chip(
    String label, {
    required bool selected,
    required VoidCallback onTap,
  }) {
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: ChoiceChip(
        label: Text(label),
        selected: selected,
        showCheckmark: false,
        onSelected: (_) => onTap(),
        selectedColor: kPrimary.withValues(alpha: .26),
        backgroundColor: kSurface,
        side: BorderSide(
          color: selected ? kPrimary.withValues(alpha: .7) : kBorder,
        ),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        labelStyle: TextStyle(
          fontSize: 12,
          fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
          color: selected ? kText : kMuted,
        ),
      ),
    );
  }

  Widget _searchBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
      child: TextField(
        controller: _search,
        onChanged: (v) => setState(() => _query = v),
        textInputAction: TextInputAction.search,
        decoration: InputDecoration(
          hintText: isNpc ? 'Buscar por nome ou tag' : 'Buscar protagonista',
          prefixIcon: const Icon(Icons.search, size: 20),
          isDense: true,
          suffixIcon: _query.isEmpty
              ? null
              : IconButton(
                  tooltip: 'Limpar',
                  icon: const Icon(Icons.close, size: 18),
                  onPressed: () {
                    _search.clear();
                    setState(() => _query = '');
                  },
                ),
        ),
      ),
    );
  }

  Widget _noResults() {
    final hasQuery = _query.trim().isNotEmpty;
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
          const Icon(Icons.search_off, size: 40, color: kMuted),
          const SizedBox(height: 10),
          Text(
            hasQuery
                ? 'Nenhum resultado para "$_query"'
                : (_showArchived
                    ? 'Nada arquivado com esse filtro'
                    : 'Nenhum ativo com esse filtro'),
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(
            hasQuery ? 'Tente outro nome ou uma tag.' : 'Ajuste os filtros.',
            style: const TextStyle(color: kMuted, fontSize: 12.5),
          ),
          const SizedBox(height: 12),
          TextButton(
            onPressed: () {
              _search.clear();
              setState(() {
                _query = '';
                _worldFilter = null;
                _tagFilter = null;
              });
            },
            child: const Text('Limpar filtros'),
          ),
        ],
      ),
    );
  }

  Widget _hero(int count) {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(24),
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
            right: -14,
            top: -18,
            child: GameIcon(
              'spell-book',
              size: 116,
              color: Colors.white.withValues(alpha: .12),
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                isNpc ? 'Toda escolha deixa uma marca.' : 'Você é o centro da história.',
                style: const TextStyle(
                  fontSize: 19,
                  fontWeight: FontWeight.w800,
                  height: 1.25,
                  letterSpacing: .2,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                isNpc
                    ? 'Crie um NPC e comece a escrever junto. O mundo lembra.'
                    : 'Crie protagonistas para dar voz e corpo às suas ações.',
                style: const TextStyle(color: kMuted, fontSize: 12.5, height: 1.45),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  _stat('$count', isNpc ? 'NPCs' : 'personagens'),
                  const SizedBox(width: 22),
                  _stat('∞', 'histórias'),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _stat(String value, String label) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Text(value,
            style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800, color: kAccent)),
        const SizedBox(width: 5),
        Padding(
          padding: const EdgeInsets.only(bottom: 3),
          child: Text(label, style: const TextStyle(color: kMuted, fontSize: 11)),
        ),
      ],
    );
  }

  Widget _card(Character c) {
    return Container(
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: kBorder),
      ),
      clipBehavior: Clip.antiAlias,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: isNpc ? () => _openSessions(c) : () => _edit(c),
          onLongPress: () => _menu(c),
          child: Stack(
            fit: StackFit.expand,
            children: [
              _cover(c),
              const DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      Color(0x0008070F),
                      Color(0x3308070F),
                      Color(0xF208070F),
                    ],
                    stops: [0, .42, 1],
                  ),
                ),
              ),
              Positioned(
                top: 8,
                left: 8,
                child: PopupMenuButton<String>(
                  tooltip: 'Mais',
                  color: kSurface2,
                  padding: EdgeInsets.zero,
                  onSelected: (v) {
                    if (v == 'play') _openSessions(c);
                    if (v == 'edit') _edit(c);
                    if (v == 'archive') _archive(c);
                    if (v == 'cover') _generateCoverFor(c);
                    if (v == 'delete') _delete(c);
                  },
                  itemBuilder: (_) => [
                    if (isNpc)
                      const PopupMenuItem(
                        value: 'play',
                        child: ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: Icon(Icons.play_arrow_rounded, color: kPrimary),
                          title: Text('Nova história'),
                        ),
                      ),
                    const PopupMenuItem(
                      value: 'edit',
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(Icons.edit_outlined),
                        title: Text('Editar'),
                      ),
                    ),
                    PopupMenuItem(
                      value: 'archive',
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(
                          c.archived
                              ? Icons.unarchive_outlined
                              : Icons.archive_outlined,
                        ),
                        title: Text(c.archived ? 'Desarquivar' : 'Arquivar'),
                      ),
                    ),
                    const PopupMenuItem(
                      value: 'cover',
                      child: ListTile(
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(Icons.auto_awesome, color: kAccent),
                        title: Text('Gerar capa'),
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
                  child: Container(
                    width: 34,
                    height: 34,
                    decoration: const BoxDecoration(
                      color: Color(0x66000000),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.more_horiz,
                        color: Colors.white, size: 19),
                  ),
                ),
              ),
              if (isNpc)
                Positioned(
                  top: 8,
                  right: 8,
                  child: _circleAction(
                    Icons.play_arrow_rounded,
                    () => _openSessions(c),
                    'Abrir histórias',
                  ),
                ),
              Positioned(
                left: 12,
                right: 12,
                bottom: 11,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (c.archived) ...[
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 6,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: Colors.black.withValues(alpha: .4),
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(
                            color: Colors.white.withValues(alpha: .18),
                          ),
                        ),
                        child: const Text(
                          'ARQUIVADO',
                          style: TextStyle(
                            fontSize: 8,
                            fontWeight: FontWeight.w700,
                            letterSpacing: .6,
                          ),
                        ),
                      ),
                      const SizedBox(height: 4),
                    ],
                    Text(
                      c.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w800,
                        letterSpacing: .1,
                      ),
                    ),
                    if (c.tagline.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(
                        c.tagline,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                          color: kText.withValues(alpha: .68),
                          fontSize: 11.5,
                          height: 1.3,
                        ),
                      ),
                    ],
                    if (c.tags.isNotEmpty) ...[
                      const SizedBox(height: 7),
                      Wrap(
                        spacing: 5,
                        runSpacing: 5,
                        children: [
                          for (final t in c.tags.take(2)) _tag(t),
                        ],
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _circleAction(IconData icon, VoidCallback onTap, String tooltip) {
    return Tooltip(
      message: tooltip,
      child: Material(
        color: const Color(0x66000000),
        shape: const CircleBorder(),
        child: InkWell(
          customBorder: const CircleBorder(),
          onTap: onTap,
          child: SizedBox(
            width: 34,
            height: 34,
            child: Icon(icon, color: Colors.white, size: 20),
          ),
        ),
      ),
    );
  }

  Widget _cover(Character c) {
    final fallback = _fallbackCover(c);
    if (c.avatar.trim().isEmpty) return fallback;
    return Image.network(
      ApiClient.mediaUrl(c.avatar),
      fit: BoxFit.cover,
      errorBuilder: (_, _, _) => fallback,
      loadingBuilder: (_, child, progress) =>
          progress == null ? child : fallback,
    );
  }

  Widget _fallbackCover(Character c) {
    const palettes = [
      [Color(0xFF3A2A78), Color(0xFF17122E)],
      [Color(0xFF4A2856), Color(0xFF1C1226)],
      [Color(0xFF234A66), Color(0xFF111C2A)],
      [Color(0xFF51402A), Color(0xFF211A12)],
      [Color(0xFF2D4A3A), Color(0xFF12201A)],
    ];
    final seed = c.name.runes.fold<int>(0, (sum, rune) => sum + rune);
    final palette = palettes[seed % palettes.length];
    return Container(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: palette,
        ),
      ),
      child: Stack(
        children: [
          Positioned(
            right: -18,
            top: -12,
            child: GameIcon(
              'spell-book',
              size: 122,
              color: Colors.white.withValues(alpha: .10),
            ),
          ),
          Center(
            child: Text(
              c.name.isNotEmpty ? c.name[0].toUpperCase() : '?',
              style: TextStyle(
                fontFamily: kSerif,
                fontSize: 74,
                fontWeight: FontWeight.w600,
                height: 1,
                color: Colors.white.withValues(alpha: .82),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _tag(String t) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: .28),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.white.withValues(alpha: .12)),
      ),
      child: Text(
        t,
        style: TextStyle(
          color: kText.withValues(alpha: .82),
          fontSize: 9.5,
          fontWeight: FontWeight.w600,
        ),
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
          Icon(Icons.person_add_alt_1, size: 44, color: kPrimary.withValues(alpha: .9)),
          const SizedBox(height: 12),
          Text(
            isNpc ? 'Nenhum NPC ainda' : 'Nenhum protagonista ainda',
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          const Text(
            'Crie o primeiro e comece a história.',
            textAlign: TextAlign.center,
            style: TextStyle(color: kMuted, fontSize: 12.5),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: _openForm,
            icon: const Icon(Icons.add),
            label: Text('Criar ${widget.addLabel}'),
          ),
        ],
      ),
    );
  }
}

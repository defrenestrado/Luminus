import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui' show ImageFilter;

import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';
import '../local_files.dart';
import '../story_text.dart';
import '../theme.dart';
import '../widgets/dice_roll.dart';
import '../widgets/fx.dart';
import '../widgets/game_icon.dart';
import '../widgets/ornate_frame.dart';

class _Msg {
  final int id;
  final String role;
  String content;
  bool streaming;
  String? status;
  Map<String, dynamic>? resolution;
  _Msg(this.role, this.content, {this.id = 0, this.streaming = false});
}

enum _ViewMode { scene, reader, table }

class ChatScreen extends StatefulWidget {
  final Character character;
  final Session session;
  const ChatScreen({super.key, required this.character, required this.session});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final List<_Msg> _msgs = [];
  final _input = TextEditingController();
  final _inputFocus = FocusNode();
  final _scroll = ScrollController();
  final AudioPlayer _player = AudioPlayer();
  final AudioPlayer _ambient = AudioPlayer();
  final AudioPlayer _sfx = AudioPlayer();
  bool _soundOn = true;
  List<Choice> _choices = [];
  List<CastEntry> _cast = [];
  bool _loading = true;
  bool _sending = false;
  bool _loadingChoices = false;
  bool _openingLoading = false;
  bool _openingFailed = false;
  bool _openingRequested = false;
  late String _sceneImage;
  late String _sessionTitle;
  bool _generatingScene = false;
  bool _ooc = false;
  bool _autoplay = false;
  bool _worldLive = false;
  Timer? _worldTimer;
  Timer? _diceTimer;
  _ViewMode _viewMode = _ViewMode.scene;
  Map<String, dynamic>? _dice;
  String _diceSeed = '';
  double _textScale = 1.0;
  bool _chaptering = false;
  int _nr = 20;
  Map<String, int> _axes = const {
    'trust': 30,
    'intimacy': 20,
    'respect': 30,
    'tension': 20,
  };
  String _vinculo = '';
  String _worldName = '';
  Character? _protagonist;
  int _chapterCount = 0;
  List<Chapter> _chapters = const [];
  Map<String, dynamic>? _sceneState;
  String _mood = '';
  Map<String, dynamic>? _pacing;
  Map<String, dynamic>? _commitments;
  List<Map<String, dynamic>>? _castFromDone;
  Map<String, dynamic>? _resolution;

  @override
  void initState() {
    super.initState();
    _sceneImage = widget.session.sceneImage;
    _sessionTitle = widget.session.title;
    _input.addListener(_onInputChanged);
    _boot();
  }

  Future<void> _boot() async {
    await _loadPrefs();
    await _load();
  }

  @override
  void dispose() {
    _worldTimer?.cancel();
    _diceTimer?.cancel();
    _input.removeListener(_onInputChanged);
    _input.dispose();
    _inputFocus.dispose();
    _scroll.dispose();
    _player.dispose();
    _ambient.dispose();
    _sfx.dispose();
    super.dispose();
  }

  Future<void> _loadPrefs() async {
    final p = await SharedPreferences.getInstance();
    final rawMode =
        p.getString('view_mode_${widget.session.id}') ??
        p.getString('view_mode') ??
        'scene';
    final mode = _ViewMode.values.firstWhere(
      (m) => m.name == rawMode,
      orElse: () => _ViewMode.scene,
    );
    if (mounted) {
      setState(() {
        _autoplay = p.getBool('autoplay') ?? false;
        _worldLive = p.getBool('world_live') ?? false;
        _textScale = p.getDouble('text_scale') ?? 1.0;
        _soundOn = p.getBool('sound_on') ?? true;
        _viewMode = mode;
      });
      if (_soundOn) _startAmbient();
    }
  }

  Future<void> _toggleAutoplay() async {
    final p = await SharedPreferences.getInstance();
    final v = !_autoplay;
    await p.setBool('autoplay', v);
    if (mounted) setState(() => _autoplay = v);
  }

  Future<void> _toggleWorldLive() async {
    final p = await SharedPreferences.getInstance();
    final v = !_worldLive;
    await p.setBool('world_live', v);
    if (!mounted) return;
    setState(() => _worldLive = v);
    if (v) {
      _armWorldTimer();
      _snack('Mundo vivo: ON — a cena continua sozinha.');
    } else {
      _cancelWorldTimer();
      _snack('Mundo vivo: OFF.');
    }
  }

  void _cancelWorldTimer() {
    _worldTimer?.cancel();
    _worldTimer = null;
  }

  void _armWorldTimer() {
    _cancelWorldTimer();
    if (!_worldLive || !mounted) return;
    if (_sending || _openingLoading || _generatingScene || _chaptering) return;
    if (_input.text.trim().isNotEmpty || _choices.isNotEmpty) return;
    _worldTimer = Timer(const Duration(seconds: 8), () {
      if (!mounted || !_worldLive) return;
      if (_sending || _openingLoading || _generatingScene) return;
      if (_input.text.trim().isNotEmpty || _choices.isNotEmpty) return;
      if (_chaptering) {
        _armWorldTimer();
        return;
      }
      _continueScene();
    });
  }

  void _onInputChanged() {
    if (_input.text.trim().isNotEmpty) {
      _cancelWorldTimer();
    } else {
      _armWorldTimer();
    }
  }

  Future<void> _setViewMode(_ViewMode mode) async {
    final p = await SharedPreferences.getInstance();
    await p.setString('view_mode_${widget.session.id}', mode.name);
    await p.setString('view_mode', mode.name);
    if (mounted) setState(() => _viewMode = mode);
  }

  String _viewModeLabel(_ViewMode mode) {
    switch (mode) {
      case _ViewMode.scene:
        return 'Cena';
      case _ViewMode.reader:
        return 'Leitura';
      case _ViewMode.table:
        return 'Mesa';
    }
  }

  Future<void> _pickViewMode() async {
    final picked = await showModalBottomSheet<_ViewMode>(
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
            for (final mode in _ViewMode.values)
              ListTile(
                leading: Icon(
                  mode == _ViewMode.scene
                      ? Icons.theater_comedy_outlined
                      : mode == _ViewMode.reader
                      ? Icons.menu_book_outlined
                      : Icons.forum_outlined,
                  color: _viewMode == mode ? kAccent : kMuted,
                ),
                title: Text(_viewModeLabel(mode)),
                subtitle: Text(
                  mode == _ViewMode.scene
                      ? 'visual novel: retrato, fundo e diálogo'
                      : mode == _ViewMode.reader
                      ? 'prosa em largura total, estilo livro'
                      : 'chat com ficha, dados e histórico',
                  style: const TextStyle(color: kMuted, fontSize: 11.5),
                ),
                trailing: _viewMode == mode
                    ? const Icon(Icons.check, color: kAccent, size: 18)
                    : null,
                onTap: () {
                  Navigator.pop(context);
                  _setViewMode(mode);
                },
              ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
    if (picked != null) await _setViewMode(picked);
  }

  void _showDice(Map<String, dynamic> receipt) {
    final seed = (receipt['seed'] ?? '').toString();
    if (seed.isNotEmpty && seed == _diceSeed) return;
    _diceSeed = seed;
    _diceTimer?.cancel();
    if (!mounted) return;
    HapticFeedback.mediumImpact();
    _playSfx('dice_roll.wav', volume: .55);
    setState(() => _dice = receipt);
    _diceTimer = Timer(const Duration(milliseconds: 2600), () {
      if (mounted) setState(() => _dice = null);
    });
  }

  void _hideDice() {
    _diceTimer?.cancel();
    if (mounted) setState(() => _dice = null);
  }

  String _cleanForSpeech(String t) => t
      .replaceAll('*', '')
      .replaceAll('#', '')
      .replaceAll('_', '')
      .replaceAll(RegExp(r'\s+'), ' ')
      .trim();

  Future<void> _speak(String text) async {
    final clean = _cleanForSpeech(text);
    if (clean.isEmpty) return;
    try {
      final bytes = await ApiClient.tts(clean, voice: widget.character.voice);
      await _player.stop();
      await _player.play(BytesSource(bytes));
    } catch (_) {}
  }

  Future<void> _startAmbient() async {
    try {
      await _ambient.setReleaseMode(ReleaseMode.loop);
      await _ambient.setVolume(.16);
      await _ambient.play(AssetSource('audio/ambient_soft.wav'));
    } catch (_) {}
  }

  Future<void> _stopAmbient() async {
    try {
      await _ambient.stop();
    } catch (_) {}
  }

  Future<void> _playSfx(String asset, {double volume = .4}) async {
    if (!_soundOn) return;
    try {
      await _sfx.stop();
      await _sfx.setVolume(volume);
      await _sfx.play(AssetSource('audio/$asset'));
    } catch (_) {}
  }

  Future<void> _toggleSound() async {
    final prefs = await SharedPreferences.getInstance();
    final value = !_soundOn;
    await prefs.setBool('sound_on', value);
    if (!mounted) return;
    setState(() => _soundOn = value);
    if (value) {
      _startAmbient();
    } else {
      _stopAmbient();
    }
  }

  Future<void> _load() async {
    try {
      final msgs = await ApiClient.messages(widget.session.id);
      if (!mounted) return;
      setState(() {
        _msgs.clear();
        for (final m in msgs) {
          _msgs.add(
            _Msg(m.role, m.content, id: m.id)..resolution = m.resolution,
          );
        }
        _loading = false;
      });
      _scrollDown();
      _loadRelationship();
      _loadWorld();
      _loadProtagonist();
      _loadChapters();
      _checkChapter();
      final needsOpening = !msgs.any((m) => m.role == 'user');
      if (needsOpening) {
        await _ensureOpening();
      } else {
        _loadChoices();
      }
      if (_autoplay) {
        final last = _msgs.lastWhere(
          (m) => m.role == 'assistant' && m.content.trim().isNotEmpty,
          orElse: () => _Msg('', ''),
        );
        if (last.content.trim().isNotEmpty) _speak(last.content);
      }
      WidgetsBinding.instance.addPostFrameCallback((_) {
        _maybeShowDigest();
      });
    } catch (e) {
      if (mounted) {
        setState(() => _loading = false);
        _snack(ApiClient.friendly(e));
      }
    }
  }

  Future<void> _ensureOpening() async {
    if (_openingRequested || _openingLoading) return;
    _openingRequested = true;
    if (mounted) {
      setState(() {
        _openingLoading = true;
        _openingFailed = false;
      });
    }
    try {
      final d = await ApiClient.opening(widget.session.id);
      final msg = d['message'];
      final content = (msg is Map ? (msg['content'] ?? '') : '')
          .toString()
          .trim();
      final id = (msg is Map ? (msg['id'] as num?) : null)?.toInt() ?? 0;
      final title = (d['title'] ?? '').toString().trim();
      if (!mounted) return;
      if (content.isNotEmpty) {
        setState(() {
          if (title.isNotEmpty) _sessionTitle = title;
          final idx = _msgs.indexWhere((m) => m.role == 'assistant');
          if (idx >= 0) {
            _msgs[idx] = _Msg('assistant', content, id: id);
          } else {
            _msgs.insert(0, _Msg('assistant', content, id: id));
          }
          _openingFailed = d['generated'] != true && d['error'] != null;
        });
        _scrollDown();
        _loadChoices();
      } else {
        setState(() => _openingFailed = true);
      }
    } catch (_) {
      if (mounted) setState(() => _openingFailed = true);
    } finally {
      _openingRequested = false;
      if (mounted) setState(() => _openingLoading = false);
    }
  }

  void _scrollDown() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _loadChoices() async {
    if (_openingLoading) return;
    setState(() {
      _loadingChoices = true;
      _choices = [];
    });
    try {
      final c = await ApiClient.choices(widget.session.id);
      if (mounted) setState(() => _choices = c);
    } catch (_) {
    } finally {
      if (mounted) setState(() => _loadingChoices = false);
    }
    if (_choices.isNotEmpty) {
      _cancelWorldTimer();
    } else {
      _armWorldTimer();
    }
  }

  Future<void> _loadWorld() async {
    final wid = widget.character.worldId;
    if (wid == null) return;
    try {
      final worlds = await ApiClient.worlds();
      for (final w in worlds) {
        if (w.id == wid) {
          if (mounted) setState(() => _worldName = w.name);
          return;
        }
      }
    } catch (_) {}
  }

  Future<void> _loadProtagonist() async {
    final pid = widget.session.protagonistId;
    if (pid == null) return;
    try {
      final p = await ApiClient.character(pid);
      if (mounted) setState(() => _protagonist = p);
    } catch (_) {}
  }

  Future<void> _generateScene() async {
    if (_generatingScene) return;
    setState(() => _generatingScene = true);
    try {
      final d = await ApiClient.generateScene(widget.session.id);
      final url = (d['url'] ?? '').toString();
      if (url.isEmpty) throw 'A API não retornou a imagem.';
      if (!mounted) return;
      setState(() => _sceneImage = url);
      final warning = (d['warning'] ?? '').toString();
      _snack(warning.isNotEmpty ? warning : 'Cena ilustrada.');
    } catch (e) {
      _snack(e is String ? e : ApiClient.friendly(e));
    } finally {
      if (mounted) setState(() => _generatingScene = false);
    }
  }

  Future<void> _loadRelationship() async {
    try {
      final info = await ApiClient.sessionCast(widget.session.id);
      if (!mounted) return;
      setState(() {
        _nr = info.mainNr;
        if (info.mainAxes.isNotEmpty) _axes = info.mainAxes;
        _vinculo = info.mainVinculo;
        _cast = info.cast;
      });
    } catch (_) {}
  }

  String _nrTier(int n) {
    if (n < 25) return 'Fechada: só o superficial.';
    if (n < 45) return 'Cautelosa: gostos, lembranças leves e medos pequenos.';
    if (n < 65) {
      return 'Aberta: vulnerabilidade e parte do passado; o núcleo protegido.';
    }
    if (n < 85) return 'Confiante: segredos importantes e medos reais.';
    return 'Total: o segredo central e o medo mais profundo.';
  }

  Widget _sheetSection(String title) {
    return Padding(
      padding: const EdgeInsets.only(top: 18, bottom: 8),
      child: Text(
        title.toUpperCase(),
        style: const TextStyle(
          color: kMuted,
          fontSize: 10.5,
          fontWeight: FontWeight.w700,
          letterSpacing: 1.3,
        ),
      ),
    );
  }

  Widget _sheetChips(List<String> values, {Color color = kText}) {
    if (values.isEmpty) {
      return const Text(
        'Nada registrado ainda.',
        style: TextStyle(color: kMuted, fontSize: 12),
      );
    }
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final value in values)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
            decoration: BoxDecoration(
              color: kSurface2,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: kBorder),
            ),
            child: Text(value, style: TextStyle(color: color, fontSize: 11.5)),
          ),
      ],
    );
  }

  Future<void> _showSheet() async {
    Map<String, dynamic> data;
    try {
      data = await ApiClient.sheet(widget.session.id);
    } catch (e) {
      _snack(ApiClient.friendly(e));
      return;
    }
    if (!mounted) return;
    List<String> strings(Object? raw) => ((raw as List?) ?? const [])
        .map((e) => e.toString().trim())
        .where((e) => e.isNotEmpty)
        .toList();
    final inventory = strings(data['inventory']);
    final injuries = strings(data['injuries']);
    final inventoryDetail = ((data['inventory_detail'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final injuriesDetail = ((data['injuries_detail'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final inventoryLabels = inventoryDetail.isEmpty
        ? inventory
        : [
            for (final item in inventoryDetail)
              (item['effect_label'] ?? '').toString().isEmpty
                  ? (item['name'] ?? '').toString()
                  : '${item['name']} · ${item['effect_label']}',
          ];
    final injuryLabels = injuriesDetail.isEmpty
        ? injuries
        : [
            for (final injury in injuriesDetail)
              '${injury['name']} · teste -${injury['penalty']} · CD +${injury['dc']}',
          ];
    final facts = strings(data['facts']);
    final threads = ((data['threads'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final protagonist = (data['protagonist'] as Map?)?.map(
      (k, v) => MapEntry(k.toString(), v),
    );
    final axes = ((data['axes'] as Map?) ?? const {}).map(
      (k, v) => MapEntry(k.toString(), (v as num?)?.toInt() ?? 0),
    );
    final attributes = ((data['attributes'] as Map?) ?? const {}).map(
      (k, v) => MapEntry(k.toString(), (v as num?)?.toInt() ?? 10),
    );
    final skills = ((data['skills'] as Map?) ?? const {}).map(
      (k, v) => MapEntry(k.toString(), (v as num?)?.toInt() ?? 0),
    );
    final trainedSkills = [
      for (final entry in skills.entries)
        if (entry.value > 0) '${_skillLabel(entry.key)} ${entry.value}',
    ];
    final modifiers = ((data['modifiers'] as Map?) ?? const {}).map(
      (k, v) => MapEntry(k.toString(), (v as Map?) ?? const {}),
    );
    final nr = (data['nr'] as num?)?.toInt() ?? _nr;
    final vinculo = (data['vinculo'] ?? '').toString();
    const labels = {
      'trust': 'Confiança',
      'intimacy': 'Intimidade',
      'respect': 'Respeito',
      'tension': 'Tensão',
    };
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .78,
        maxChildSize: .95,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
          children: [
            Row(
              children: [
                const Expanded(
                  child: Text(
                    'Ficha',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
                  ),
                ),
                Text(
                  'NR $nr',
                  style: const TextStyle(
                    color: kAccent,
                    fontSize: 15,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ],
            ),
            if (protagonist != null) ...[
              const SizedBox(height: 12),
              Row(
                children: [
                  LuminusAvatar(
                    radius: 24,
                    url: (protagonist['avatar'] ?? '').toString(),
                    letter: (protagonist['name'] ?? '?').toString().isEmpty
                        ? '?'
                        : (protagonist['name'] ?? '?').toString()[0],
                    ring: true,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          (protagonist['name'] ?? 'Protagonista').toString(),
                          style: const TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        if ((protagonist['tagline'] ?? '')
                            .toString()
                            .isNotEmpty)
                          Text(
                            protagonist['tagline'].toString(),
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
                ],
              ),
            ],
            const SizedBox(height: 10),
            Text(
              _nrTier(nr),
              style: const TextStyle(color: kText, fontSize: 12.5, height: 1.4),
            ),
            if (vinculo.isNotEmpty) ...[
              const SizedBox(height: 5),
              Text(
                '"$vinculo"',
                style: const TextStyle(
                  color: kMuted,
                  fontSize: 11.5,
                  fontStyle: FontStyle.italic,
                ),
              ),
            ],
            const SizedBox(height: 12),
            for (final entry in labels.entries) ...[
              Row(
                children: [
                  Text(
                    entry.value,
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const Spacer(),
                  Text(
                    '${axes[entry.key] ?? 0}',
                    style: const TextStyle(color: kMuted, fontSize: 11),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: LinearProgressIndicator(
                  value: (((axes[entry.key] ?? 0) / 100).clamp(
                    0.0,
                    1.0,
                  )).toDouble(),
                  minHeight: 6,
                  backgroundColor: kSurface2,
                  color: entry.key == 'tension' ? kDanger : kPrimary,
                ),
              ),
              const SizedBox(height: 10),
            ],
            if (attributes.isNotEmpty) ...[
              _sheetSection('Atributos'),
              _sheetChips([
                for (final entry in attributes.entries)
                  '${_attributeLabel(entry.key)} ${entry.value}',
              ]),
              _sheetSection('Perícias'),
              _sheetChips(
                trainedSkills.isEmpty ? const ['sem treino'] : trainedSkills,
              ),
              if (modifiers.isNotEmpty) ...[
                _sheetSection('Modificadores por teste'),
                _sheetChips([
                  for (final entry in modifiers.entries)
                    _modifierLabel(entry.key, entry.value),
                ]),
              ],
            ],
            _sheetSection('Inventário'),
            _sheetChips(inventoryLabels),
            _sheetSection('Ferimentos'),
            _sheetChips(injuryLabels, color: kDanger),
            _sheetSection('Fios abertos'),
            if (threads.isEmpty)
              const Text(
                'Nenhum fio aberto.',
                style: TextStyle(color: kMuted, fontSize: 12),
              )
            else
              for (final thread in threads)
                Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: kSurface2,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: kBorder),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        (thread['title'] ?? 'Fio').toString(),
                        style: const TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      const SizedBox(height: 3),
                      Text(
                        [
                          (thread['kind'] ?? 'fio').toString(),
                          if (((thread['due_turn'] as num?)?.toInt() ?? 0) > 0)
                            'prazo: turno ${thread['due_turn']}',
                          if (((thread['entities'] as List?) ?? const [])
                              .isNotEmpty)
                            'envolve: ${(thread['entities'] as List).join(', ')}',
                        ].join(' · '),
                        style: const TextStyle(
                          color: kMuted,
                          fontSize: 11,
                          height: 1.4,
                        ),
                      ),
                    ],
                  ),
                ),
            _sheetSection('Fatos'),
            if (facts.isEmpty)
              const Text(
                'Nenhum fato registrado.',
                style: TextStyle(color: kMuted, fontSize: 12),
              )
            else
              for (final fact in facts.take(10))
                Padding(
                  padding: const EdgeInsets.only(bottom: 5),
                  child: Text(
                    '· $fact',
                    style: const TextStyle(fontSize: 12.5, height: 1.4),
                  ),
                ),
          ],
        ),
      ),
    );
  }

  Future<void> _showRelationship() async {
    await _loadRelationship();
    if (!mounted) return;
    const labels = {
      'trust': 'Confiança',
      'intimacy': 'Intimidade',
      'respect': 'Respeito',
      'tension': 'Tensão',
    };
    await showModalBottomSheet(
      context: context,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Vínculo com ${widget.character.name}',
                style: const TextStyle(
                  fontSize: 18,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Text(
                    'NR $_nr',
                    style: const TextStyle(
                      fontSize: 22,
                      fontWeight: FontWeight.w800,
                      color: kAccent,
                    ),
                  ),
                  const Text(
                    ' / 100',
                    style: TextStyle(color: kMuted, fontSize: 12),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: LinearProgressIndicator(
                  value: _nr / 100,
                  minHeight: 10,
                  backgroundColor: kSurface2,
                  color: kAccent,
                ),
              ),
              const SizedBox(height: 10),
              Text(
                _nrTier(_nr),
                style: const TextStyle(color: kText, fontSize: 13, height: 1.4),
              ),
              if (_vinculo.isNotEmpty) ...[
                const SizedBox(height: 8),
                Text(
                  '"$_vinculo"',
                  style: const TextStyle(
                    color: kMuted,
                    fontSize: 12,
                    fontStyle: FontStyle.italic,
                  ),
                ),
              ],
              const Divider(height: 28),
              for (final e in labels.entries) ...[
                Row(
                  children: [
                    Text(
                      e.value,
                      style: const TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const Spacer(),
                    Text(
                      '${_axes[e.key] ?? 0}',
                      style: const TextStyle(color: kMuted, fontSize: 11.5),
                    ),
                  ],
                ),
                const SizedBox(height: 5),
                ClipRRect(
                  borderRadius: BorderRadius.circular(6),
                  child: LinearProgressIndicator(
                    value: (((_axes[e.key] ?? 0) / 100).clamp(
                      0.0,
                      1.0,
                    )).toDouble(),
                    minHeight: 6,
                    backgroundColor: kSurface2,
                    color: e.key == 'tension' ? kDanger : kPrimary,
                  ),
                ),
                const SizedBox(height: 12),
              ],
              const Divider(height: 28),
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      'Elenco',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  TextButton.icon(
                    onPressed: () {
                      Navigator.pop(context);
                      _manageCast();
                    },
                    icon: const Icon(Icons.group_add, size: 16),
                    label: const Text('Gerenciar'),
                  ),
                ],
              ),
              if (_cast.isEmpty)
                const Padding(
                  padding: EdgeInsets.only(bottom: 8),
                  child: Text(
                    'Nenhum NPC secundário no elenco. Adicione um para contracenar.',
                    style: TextStyle(color: kMuted, fontSize: 12, height: 1.4),
                  ),
                ),
              for (final m in _cast) ...[
                Row(
                  children: [
                    LuminusAvatar(
                      radius: 14,
                      url: m.character.avatar,
                      letter: m.character.name.isNotEmpty
                          ? m.character.name[0]
                          : '?',
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        m.character.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 13.5,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    Text(
                      'NR ${m.nr}',
                      style: const TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w700,
                        color: kAccent,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                ClipRRect(
                  borderRadius: BorderRadius.circular(6),
                  child: LinearProgressIndicator(
                    value: (m.nr / 100).clamp(0.0, 1.0),
                    minHeight: 6,
                    backgroundColor: kSurface2,
                    color: kAccent,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  _nrTier(m.nr),
                  style: const TextStyle(
                    color: kText,
                    fontSize: 11.5,
                    height: 1.35,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  'Confiança ${m.axes['trust'] ?? 0} · Intimidade ${m.axes['intimacy'] ?? 0} · '
                  'Respeito ${m.axes['respect'] ?? 0} · Tensão ${m.axes['tension'] ?? 0}',
                  style: const TextStyle(color: kMuted, fontSize: 10.5),
                ),
                if (m.vinculo.isNotEmpty) ...[
                  const SizedBox(height: 3),
                  Text(
                    '"${m.vinculo}"',
                    style: const TextStyle(
                      color: kMuted,
                      fontSize: 11,
                      fontStyle: FontStyle.italic,
                    ),
                  ),
                ],
                const SizedBox(height: 14),
              ],
              const Text(
                'O NR sobe com escuta, honestidade, respeito e afeto; cai com invasão, desprezo e ameaça. Segredos e medos aparecem conforme o nível.',
                style: TextStyle(color: kMuted, fontSize: 11.5, height: 1.45),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _manageCast() async {
    List<Character> npcs;
    try {
      npcs = await ApiClient.characters(kind: 'npc');
    } catch (_) {
      _snack('Não consegui carregar os NPCs.');
      return;
    }
    npcs = npcs
        .where(
          (c) =>
              c.id != widget.character.id &&
              (widget.character.worldId == null ||
                  c.worldId == widget.character.worldId),
        )
        .toList();
    if (!mounted) return;
    if (npcs.isEmpty) {
      _snack('Não há outros NPCs neste mundo para adicionar.');
      return;
    }
    final selected = _cast.map((m) => m.character.id).toSet();
    final result = await showDialog<Set<int>>(
      context: context,
      builder: (_) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          backgroundColor: kSurface,
          title: const Text('Elenco'),
          content: SizedBox(
            width: double.maxFinite,
            child: ListView(
              shrinkWrap: true,
              children: [
                const Padding(
                  padding: EdgeInsets.only(bottom: 8),
                  child: Text(
                    'Marque até 4 NPCs. Cada um tem ficha, presença e NR próprios.',
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
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancelar'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, selected),
              child: const Text('Salvar'),
            ),
          ],
        ),
      ),
    );
    if (result == null) return;
    final current = _cast.map((m) => m.character.id).toSet();
    try {
      for (final id in result.difference(current)) {
        await ApiClient.addCast(widget.session.id, id);
      }
      for (final id in current.difference(result)) {
        await ApiClient.removeCast(widget.session.id, id);
      }
      await _loadRelationship();
      _snack('Elenco atualizado.');
    } catch (_) {
      _snack('Não consegui atualizar o elenco.');
    }
  }

  Future<void> _streamReply({
    String message = '',
    bool ooc = false,
    bool continueScene = false,
    bool regen = false,
    _Msg? userBubble,
  }) async {
    final reply = _Msg('assistant', '', streaming: true);
    _cancelWorldTimer();
    setState(() {
      if (userBubble != null) _msgs.add(userBubble);
      _msgs.add(reply);
      _sending = true;
      _choices = [];
      _loadingChoices = false;
    });
    _scrollDown();
    try {
      final stream = ApiClient.chatStream(
        sessionId: widget.session.id,
        message: message,
        model: widget.character.model,
        ooc: ooc,
        continueScene: continueScene,
        regen: regen,
        requestId:
            '${widget.session.id}-${DateTime.now().microsecondsSinceEpoch}',
      );
      await for (final chunk in stream) {
        if (chunk.status != null) {
          setState(() => reply.status = chunk.status);
        }
        if (chunk.delta != null) {
          setState(() => reply.content += chunk.delta!);
          _scrollDown();
        }
        if (chunk.replace != null) {
          setState(() => reply.content = chunk.replace!);
        }
        if (chunk.nr != null) {
          setState(() {
            _nr = chunk.nr!;
            if (chunk.axes != null) _axes = chunk.axes!;
            if (chunk.vinculo != null) _vinculo = chunk.vinculo!;
          });
        }
        if (chunk.scene != null) {
          setState(() => _sceneState = chunk.scene);
        }
        if (chunk.mood != null && chunk.mood!.isNotEmpty) {
          setState(() => _mood = chunk.mood!);
        }
        if (chunk.pacing != null) {
          setState(() => _pacing = chunk.pacing);
        }
        if (chunk.commitments != null) {
          setState(() => _commitments = chunk.commitments);
        }
        if (chunk.cast != null) {
          setState(() => _castFromDone = chunk.cast);
        }
        if (chunk.resolution != null) {
          setState(() {
            _resolution = chunk.resolution;
            reply.resolution = chunk.resolution;
          });
          // Na reescrita o recibo é o mesmo do turno; não faz sentido
          // reencenar o dado, só manter o cartão autorizado.
          if (!regen) _showDice(chunk.resolution!);
        }
      }
    } catch (e) {
      final friendly = ApiClient.friendly(e);
      if (!mounted) return;
      setState(() {
        if (reply.content.trim().isEmpty) {
          _msgs.remove(reply);
        } else {
          reply.content += '\n\n_($friendly)_';
        }
      });
      _snack(
        friendly,
        action: userBubble != null
            ? SnackBarAction(
                label: 'Tentar de novo',
                onPressed: () => _streamReply(regen: true),
              )
            : null,
      );
    } finally {
      setState(() {
        reply.streaming = false;
        reply.status = null;
        _sending = false;
      });
      _scrollDown();
      _loadChoices();
      _checkChapter();
      _loadRelationship();
      if (_autoplay && reply.content.trim().isNotEmpty) _speak(reply.content);
      _armWorldTimer();
    }
  }

  Future<void> _loadChapters() async {
    try {
      final chapters = await ApiClient.chapters(widget.session.id);
      if (!mounted) return;
      setState(() {
        _chapters = chapters;
        _chapterCount = chapters.length;
      });
    } catch (_) {}
  }

  Future<void> _showChapterRecap(Map<String, dynamic> chapter) async {
    final number = (chapter['number'] as num?)?.toInt() ?? 0;
    final summary = (chapter['summary'] ?? '').toString().trim();
    await showDialog<void>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: kSurface,
        title: Text('Capítulo $number'),
        content: SingleChildScrollView(
          child: Text(
            summary.isEmpty ? 'O capítulo terminou.' : summary,
            style: storyStyle(size: 15.5 * _textScale, height: 1.6),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Continuar'),
          ),
        ],
      ),
    );
  }

  Future<void> _showChapters() async {
    await _loadChapters();
    if (!mounted) return;
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .72,
        maxChildSize: .95,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 26),
          children: [
            const Text(
              'Capítulos',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            const Text(
              'Recaps automáticos da campanha.',
              style: TextStyle(color: kMuted, fontSize: 12),
            ),
            const Divider(height: 26),
            if (_chapters.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 24),
                child: Text(
                  'Nenhum capítulo fechado ainda. Continue jogando; o diretor resume a história quando o arco pedir.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: kMuted, height: 1.5),
                ),
              ),
            for (final chapter in _chapters)
              Container(
                margin: const EdgeInsets.only(bottom: 10),
                padding: const EdgeInsets.all(13),
                decoration: BoxDecoration(
                  color: kSurface2,
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: kBorder),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Capítulo ${chapter.number}',
                      style: const TextStyle(
                        color: kAccent,
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 1,
                      ),
                    ),
                    const SizedBox(height: 5),
                    Text(
                      chapter.summary,
                      style: const TextStyle(fontSize: 13, height: 1.5),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _checkChapter() async {
    if (_chaptering) return;
    try {
      final st = await ApiClient.chapterStatus(widget.session.id);
      if (mounted) {
        setState(() {
          _chapterCount = (st['chapters'] as num?)?.toInt() ?? _chapterCount;
        });
      }
      if (st['chapter_due'] == true && mounted) {
        setState(() => _chaptering = true);
        try {
          final res = await ApiClient.createChapter(widget.session.id);
          final created = res['created'];
          if (mounted && created is Map) {
            final chapter = created.map((k, v) => MapEntry(k.toString(), v));
            setState(() {
              _chapters = [..._chapters, Chapter.fromJson(chapter)];
              _chapterCount = _chapters.length;
              _chaptering = false;
            });
            await _showChapterRecap(chapter);
          }
        } finally {
          if (mounted) setState(() => _chaptering = false);
        }
      }
    } catch (_) {}
    _armWorldTimer();
  }

  Future<void> _sendText(String text, {bool ooc = false}) async {
    if (text.isEmpty ||
        _sending ||
        _openingLoading ||
        _generatingScene ||
        _chaptering) {
      return;
    }
    await _streamReply(
      message: text,
      ooc: ooc,
      userBubble: _Msg('user', ooc ? '[OOC] $text' : text),
    );
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _sending || _openingLoading || _generatingScene) return;
    _input.clear();
    final ooc = _ooc;
    if (ooc) setState(() => _ooc = false);
    await _sendText(text, ooc: ooc);
  }

  void _focusComposer(String prefix) {
    _input.text = prefix;
    _input.selection = TextSelection.collapsed(offset: prefix.length);
    _inputFocus.requestFocus();
  }

  Future<void> _continueScene() async {
    if (_sending || _openingLoading || _generatingScene || _chaptering) return;
    await _streamReply(continueScene: true);
  }

  bool _canRegen(_Msg m) =>
      m.role == 'assistant' &&
      m.id > 0 &&
      _msgs.isNotEmpty &&
      _msgs.last.id == m.id;

  Future<bool> _confirm(String title, String body) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Confirmar'),
          ),
        ],
      ),
    );
    return ok == true;
  }

  void _snack(String text, {SnackBarAction? action}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(SnackBar(content: Text(text), action: action));
  }

  Future<void> _copy(_Msg m) async {
    await Clipboard.setData(ClipboardData(text: m.content));
    _snack('Copiado.');
  }

  Future<void> _copyStory() async {
    final text = _msgs
        .where((m) => m.content.trim().isNotEmpty)
        .map(
          (m) =>
              '${m.role == 'user' ? 'Eu' : widget.character.name}: ${m.content}',
        )
        .join('\n\n');
    await Clipboard.setData(ClipboardData(text: text));
    _snack('História copiada (${_msgs.length} mensagens).');
  }

  Future<void> _edit(_Msg m) async {
    final isUser = m.role == 'user';
    final ctrl = TextEditingController(text: m.content);
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(isUser ? 'Editar sua mensagem' : 'Editar resposta'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              TextField(
                controller: ctrl,
                minLines: 3,
                maxLines: 12,
                autofocus: true,
                decoration: const InputDecoration(border: OutlineInputBorder()),
              ),
              if (isUser) ...[
                const SizedBox(height: 12),
                const Text(
                  'Ao salvar, as mensagens seguintes serão apagadas e a resposta será reescrita a partir daqui.',
                  style: TextStyle(color: kMuted, fontSize: 12, height: 1.4),
                ),
              ],
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Salvar'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final text = ctrl.text.trim();
    if (text.isEmpty) return;
    try {
      await ApiClient.updateMessage(widget.session.id, m.id, text);
      if (isUser) {
        await ApiClient.rewindSession(widget.session.id, m.id);
        final idx = _msgs.indexOf(m);
        if (idx >= 0) {
          setState(() {
            m.content = text;
            _msgs.removeRange(idx + 1, _msgs.length);
          });
        }
        await _streamReply(regen: true, message: text);
      } else {
        setState(() => m.content = text);
        _snack('Salvo.');
      }
    } catch (e) {
      _snack(ApiClient.friendly(e));
    }
  }

  Future<void> _deleteMessage(_Msg m) async {
    final ok = await _confirm(
      'Apagar mensagem?',
      'Ela será removida desta história.',
    );
    if (!ok) return;
    try {
      await ApiClient.deleteMessage(widget.session.id, m.id);
      if (!mounted) return;
      setState(() => _msgs.removeWhere((x) => x.id == m.id));
      final canRewrite =
          m.role == 'assistant' &&
          _msgs.isNotEmpty &&
          _msgs.last.role == 'user';
      _snack(
        'Mensagem apagada.',
        action: canRewrite
            ? SnackBarAction(
                label: 'Reescrever',
                onPressed: () => _streamReply(regen: true),
              )
            : null,
      );
    } catch (e) {
      _snack(ApiClient.friendly(e));
    }
  }

  Future<void> _regenerate(_Msg m) async {
    if (_sending) return;
    final ok = await _confirm(
      'Reescrever esta resposta?',
      'A resposta atual será apagada e gerada de novo, usando a mesma ação.',
    );
    if (!ok) return;
    _Msg? lastUser;
    for (final x in _msgs.reversed) {
      if (x.role == 'user') {
        lastUser = x;
        break;
      }
    }
    try {
      if (lastUser != null && lastUser.id > 0) {
        // O rewind apaga a resposta e guarda o recibo dela para o regen.
        await ApiClient.rewindSession(widget.session.id, lastUser.id);
        if (!mounted) return;
        setState(() => _msgs.removeWhere((x) => x.id > lastUser!.id));
      } else {
        await ApiClient.deleteMessage(widget.session.id, m.id);
        if (!mounted) return;
        setState(() => _msgs.removeWhere((x) => x.id == m.id));
      }
      await _streamReply(regen: true);
    } catch (e) {
      _snack(ApiClient.friendly(e));
    }
  }

  Future<void> _showActions(_Msg m) async {
    if (_sending) return;
    final isUser = m.role == 'user';
    await showModalBottomSheet(
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
            ListTile(
              leading: const Icon(Icons.copy_all_outlined),
              title: const Text('Copiar'),
              onTap: () {
                Navigator.pop(context);
                _copy(m);
              },
            ),
            if (!isUser)
              ListTile(
                leading: const Icon(Icons.volume_up_outlined),
                title: const Text('Ouvir'),
                onTap: () {
                  Navigator.pop(context);
                  _speak(m.content);
                },
              ),
            ListTile(
              leading: const Icon(Icons.edit_outlined),
              title: Text(isUser ? 'Editar e reescrever' : 'Editar e salvar'),
              onTap: () {
                Navigator.pop(context);
                _edit(m);
              },
            ),
            if (_canRegen(m))
              ListTile(
                leading: const Icon(Icons.refresh, color: kPrimary),
                title: const Text('Regenerar'),
                subtitle: const Text('apaga e gera de novo'),
                onTap: () {
                  Navigator.pop(context);
                  _regenerate(m);
                },
              ),
            ListTile(
              leading: const Icon(Icons.call_split),
              title: const Text('Ramificar daqui'),
              subtitle: const Text('cria uma linha alternativa'),
              onTap: () {
                Navigator.pop(context);
                _branchAt(m);
              },
            ),
            if (m.id > 0 && _msgs.isNotEmpty && _msgs.last.id != m.id)
              ListTile(
                leading: const Icon(Icons.history),
                title: const Text('Rebobinar até aqui'),
                subtitle: const Text(
                  'apaga o que veio depois e restaura o estado',
                ),
                onTap: () {
                  Navigator.pop(context);
                  _rewindAt(m);
                },
              ),
            ListTile(
              leading: const Icon(Icons.delete_outline, color: kDanger),
              title: const Text('Apagar', style: TextStyle(color: kDanger)),
              onTap: () {
                Navigator.pop(context);
                _deleteMessage(m);
              },
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Future<void> _showComposerMenu() async {
    await showModalBottomSheet(
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
            ListTile(
              leading: const Icon(Icons.fast_forward),
              title: const Text('Continuar a cena'),
              subtitle: const Text('a narração avança sem uma ação sua'),
              onTap: () {
                Navigator.pop(context);
                _continueScene();
              },
            ),
            ListTile(
              leading: Icon(_worldLive ? Icons.public : Icons.public_off),
              title: Text(_worldLive ? 'Mundo vivo: ON' : 'Mundo vivo: OFF'),
              subtitle: const Text('a cena avança sozinha após cada resposta'),
              onTap: () {
                Navigator.pop(context);
                _toggleWorldLive();
              },
            ),
            ListTile(
              leading: const Icon(Icons.theater_comedy_outlined),
              title: Text(_ooc ? 'Desativar OOC' : 'Ativar OOC'),
              subtitle: const Text('instrução fora do personagem'),
              onTap: () {
                Navigator.pop(context);
                setState(() => _ooc = !_ooc);
              },
            ),
            ListTile(
              leading: const Icon(Icons.refresh),
              title: const Text('Atualizar sugestões'),
              onTap: () {
                Navigator.pop(context);
                _loadChoices();
              },
            ),
            ListTile(
              leading: const Icon(Icons.history),
              title: const Text('Backlog'),
              subtitle: const Text('histórico da cena'),
              onTap: () {
                Navigator.pop(context);
                _showBacklog();
              },
            ),
            ListTile(
              leading: const Icon(Icons.badge_outlined),
              title: const Text('Ficha e inventário'),
              subtitle: const Text('personagem, itens e fios'),
              onTap: () {
                Navigator.pop(context);
                _showSheet();
              },
            ),
            ListTile(
              leading: const Icon(Icons.account_tree_outlined),
              title: const Text('Diário de fios'),
              subtitle: const Text('promessas, dívidas e mistérios'),
              onTap: () {
                Navigator.pop(context);
                _showJournal();
              },
            ),
            ListTile(
              leading: const Icon(Icons.call_split),
              title: const Text('Ramificações'),
              subtitle: const Text('linhas alternativas da campanha'),
              onTap: () {
                Navigator.pop(context);
                _showBranchTree();
              },
            ),
            ListTile(
              leading: const Icon(Icons.menu_book_outlined),
              title: const Text('Códice'),
              subtitle: const Text('pessoas, lugares, lore e cartas'),
              onTap: () {
                Navigator.pop(context);
                _showCodex();
              },
            ),
            ListTile(
              leading: const Icon(Icons.public),
              title: const Text('Crônica do mundo'),
              subtitle: const Text('eventos fora de cena e cartas'),
              onTap: () {
                Navigator.pop(context);
                _openDigest();
              },
            ),
            ListTile(
              leading: const Icon(Icons.monitor_heart_outlined),
              title: const Text('Painel do Diretor'),
              subtitle: const Text('cena, estado e métricas'),
              onTap: () {
                Navigator.pop(context);
                _showDirector();
              },
            ),
            ListTile(
              leading: const Icon(Icons.search),
              title: const Text('Buscar na história'),
              subtitle: const Text('busca full-text'),
              onTap: () {
                Navigator.pop(context);
                _showSearch();
              },
            ),
            ListTile(
              leading: const Icon(Icons.ios_share),
              title: const Text('Exportar crônica'),
              subtitle: const Text('arquivo Markdown'),
              onTap: () {
                Navigator.pop(context);
                _exportChronicle();
              },
            ),
            ListTile(
              leading: const Icon(Icons.list_alt),
              title: const Text('Capítulos'),
              subtitle: const Text('recaps da campanha'),
              onTap: () {
                Navigator.pop(context);
                _showChapters();
              },
            ),
            ListTile(
              leading: const Icon(Icons.auto_stories_outlined),
              title: const Text('Memória'),
              subtitle: const Text('recap e capítulos'),
              onTap: () {
                Navigator.pop(context);
                _showMemory();
              },
            ),
            ListTile(
              leading: const Icon(Icons.public),
              title: const Text('Lore do mundo'),
              onTap: () {
                Navigator.pop(context);
                _showLore();
              },
            ),
            ListTile(
              leading: const Icon(Icons.image_outlined, color: kAccent),
              title: Text(
                _generatingScene ? 'Pintando a cena...' : 'Ilustrar cena',
              ),
              subtitle: const Text('gera um cenário do momento'),
              onTap: () {
                Navigator.pop(context);
                _generateScene();
              },
            ),
            ListTile(
              leading: Icon(_soundOn ? Icons.music_note : Icons.music_off),
              title: Text(_soundOn ? 'Som: ON' : 'Som: OFF'),
              subtitle: const Text('ambiência e efeitos'),
              onTap: () {
                Navigator.pop(context);
                _toggleSound();
              },
            ),
            ListTile(
              leading: Icon(_autoplay ? Icons.volume_up : Icons.volume_off),
              title: Text(
                _autoplay
                    ? 'Narração automatica: ON'
                    : 'Narração automatica: OFF',
              ),
              onTap: () {
                Navigator.pop(context);
                _toggleAutoplay();
              },
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
  }

  Future<void> _showLore() async {
    final wid = widget.character.worldId;
    if (wid == null) {
      _snack('Este personagem não tem mundo vinculado.');
      return;
    }
    final textCtrl = TextEditingController();
    final keysCtrl = TextEditingController();
    List<Map<String, dynamic>> facts = [];
    try {
      facts = await ApiClient.worldFacts(wid);
    } catch (_) {}
    if (!mounted) return;
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => StatefulBuilder(
        builder: (ctx, setSt) => Padding(
          padding: EdgeInsets.only(
            bottom: MediaQuery.of(ctx).viewInsets.bottom,
          ),
          child: DraggableScrollableSheet(
            expand: false,
            initialChildSize: .72,
            maxChildSize: .95,
            builder: (_, controller) => ListView(
              controller: controller,
              padding: const EdgeInsets.fromLTRB(18, 14, 18, 24),
              children: [
                const Text(
                  'Lore do mundo',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 4),
                const Text(
                  'Injetada no narrador só quando a ação toca nas palavras-chave.',
                  style: TextStyle(color: kMuted, fontSize: 12),
                ),
                const Divider(height: 26),
                if (facts.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 12),
                    child: Text(
                      'Nenhuma lore ainda.',
                      style: TextStyle(color: kMuted),
                    ),
                  ),
                for (final f in facts)
                  Container(
                    margin: const EdgeInsets.only(bottom: 8),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: kSurface2,
                      borderRadius: BorderRadius.circular(14),
                      border: Border.all(color: kBorder),
                    ),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                f['text']?.toString() ?? '',
                                style: const TextStyle(
                                  fontSize: 13.5,
                                  height: 1.4,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                'chaves: ${f['keys'] ?? ''}',
                                style: const TextStyle(
                                  color: kMuted,
                                  fontSize: 11.5,
                                ),
                              ),
                            ],
                          ),
                        ),
                        IconButton(
                          visualDensity: VisualDensity.compact,
                          icon: const Icon(
                            Icons.delete_outline,
                            size: 18,
                            color: kDanger,
                          ),
                          onPressed: () async {
                            await ApiClient.deleteFact(f['id'] as int);
                            final up = await ApiClient.worldFacts(wid);
                            setSt(() => facts = up);
                          },
                        ),
                      ],
                    ),
                  ),
                const Divider(height: 26),
                TextField(
                  controller: textCtrl,
                  minLines: 2,
                  maxLines: 4,
                  decoration: const InputDecoration(
                    labelText: 'Nova lore',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: keysCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Palavras-chave (ex.: Zetsu, qi, Academia)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                FilledButton.icon(
                  onPressed: () async {
                    final t = textCtrl.text.trim();
                    if (t.isEmpty) return;
                    await ApiClient.addWorldFact(wid, t, keysCtrl.text.trim());
                    textCtrl.clear();
                    keysCtrl.clear();
                    final up = await ApiClient.worldFacts(wid);
                    setSt(() => facts = up);
                  },
                  icon: const Icon(Icons.add),
                  label: const Text('Adicionar lore'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  String _voiceKindLabel(String kind) {
    switch (kind) {
      case 'aviso':
        return 'Aviso';
      case 'carta':
        return 'Carta';
      case 'relatorio':
        return 'Relatório';
      default:
        return 'Boato';
    }
  }

  String _voiceTruthLabel(String value) {
    switch (value) {
      case 'verdadeiro':
        return 'verdadeiro';
      case 'distorcido':
        return 'distorcido';
      case 'falso':
        return 'falso';
      default:
        return 'incerto';
    }
  }

  Color _voiceTruthColor(String value) {
    switch (value) {
      case 'verdadeiro':
        return kAccent;
      case 'falso':
        return kDanger;
      case 'distorcido':
        return kPrimary;
      default:
        return kMuted;
    }
  }

  Future<void> _showVoices() async {
    List<WorldVoice> voices = [];
    String? error;
    try {
      voices = await ApiClient.sessionVoices(widget.session.id);
    } catch (e) {
      error = e is String ? e : ApiClient.friendly(e);
    }
    if (!mounted) return;
    var busy = false;
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => StatefulBuilder(
        builder: (ctx, setSt) => DraggableScrollableSheet(
          expand: false,
          initialChildSize: .72,
          maxChildSize: .95,
          builder: (_, controller) => ListView(
            controller: controller,
            padding: const EdgeInsets.fromLTRB(18, 14, 18, 24),
            children: [
              const Text(
                'Vozes do mundo',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              const Text(
                'Cartas, boatos e avisos que circulam pelo mundo. '
                'O que o autor afirma não é automaticamente verdade.',
                style: TextStyle(color: kMuted, fontSize: 12, height: 1.4),
              ),
              const SizedBox(height: 14),
              FilledButton.icon(
                onPressed: busy
                    ? null
                    : () async {
                        setSt(() {
                          busy = true;
                          error = null;
                        });
                        try {
                          final v = await ApiClient.generateSessionVoice(
                            widget.session.id,
                          );
                          setSt(() {
                            voices = [v, ...voices];
                            busy = false;
                          });
                        } catch (e) {
                          setSt(() {
                            busy = false;
                            error = e is String ? e : ApiClient.friendly(e);
                          });
                        }
                      },
                icon: busy
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.auto_awesome, size: 18),
                label: Text(busy ? 'Gerando...' : 'Gerar artefato agora'),
              ),
              if (error != null) ...[
                const SizedBox(height: 10),
                Text(
                  error!,
                  style: const TextStyle(color: kDanger, fontSize: 12.5),
                ),
              ],
              const Divider(height: 26),
              if (voices.isEmpty)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 20),
                  child: Text(
                    'Nenhum artefato ainda. Eles também podem surgir '
                    'automaticamente a cada 6 turnos.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: kMuted, height: 1.5),
                  ),
                ),
              for (final v in voices)
                Container(
                  margin: const EdgeInsets.only(bottom: 10),
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: kSurface2,
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: kBorder),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              v.title.isEmpty ? 'Artefato' : v.title,
                              style: const TextStyle(
                                fontSize: 14,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                          Container(
                            padding: const EdgeInsets.symmetric(
                              horizontal: 8,
                              vertical: 3,
                            ),
                            decoration: BoxDecoration(
                              color: kPrimary.withValues(alpha: .16),
                              borderRadius: BorderRadius.circular(20),
                            ),
                            child: Text(
                              _voiceKindLabel(v.kind),
                              style: const TextStyle(
                                fontSize: 10.5,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'por ${v.author.isEmpty ? 'autor desconhecido' : v.author}'
                        ' • ${v.visibility == 'restrito' ? 'restrito' : 'público'}'
                        ' • ${_voiceTruthLabel(v.truthStatus)}',
                        style: TextStyle(
                          color: _voiceTruthColor(v.truthStatus),
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      if (v.body.trim().isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text(
                          v.body,
                          style: const TextStyle(fontSize: 13, height: 1.5),
                        ),
                      ],
                      if (v.knownBy.isNotEmpty) ...[
                        const SizedBox(height: 8),
                        Text(
                          'Quem sabe: ${v.knownBy.join(', ')}',
                          style: const TextStyle(
                            color: kMuted,
                            fontSize: 11,
                            height: 1.4,
                          ),
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

  Future<void> _showBranchTree() async {
    Map<String, dynamic> data;
    try {
      data = await ApiClient.sessionTree(widget.session.id);
    } catch (e) {
      _snack(ApiClient.friendly(e));
      return;
    }
    if (!mounted) return;
    final nodes = ((data['sessions'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    Future<void> open(Map<String, dynamic> node) async {
      final id = (node['id'] as num?)?.toInt() ?? 0;
      if (id <= 0 || id == widget.session.id) return;
      try {
        final session = await ApiClient.session(id);
        if (!mounted) return;
        Navigator.pop(context);
        Navigator.pushReplacement(
          context,
          MaterialPageRoute(
            builder: (_) =>
                ChatScreen(character: widget.character, session: session),
          ),
        );
      } catch (e) {
        _snack(ApiClient.friendly(e));
      }
    }

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .72,
        maxChildSize: .95,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 26),
          children: [
            const Text(
              'Ramificações',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            const Text(
              'Linhas alternativas criadas a partir desta campanha.',
              style: TextStyle(color: kMuted, fontSize: 12),
            ),
            const Divider(height: 26),
            if (nodes.length <= 1)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 24),
                child: Text(
                  'Ainda não há ramos. Use “Ramificar daqui” no menu de uma mensagem.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: kMuted, height: 1.5),
                ),
              ),
            for (final node in nodes)
              Padding(
                padding: EdgeInsets.only(
                  left: ((node['depth'] as num?)?.toInt() ?? 0) * 16.0,
                  bottom: 8,
                ),
                child: Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: node['current'] == true
                        ? kPrimary.withValues(alpha: .13)
                        : kSurface2,
                    borderRadius: BorderRadius.circular(13),
                    border: Border.all(
                      color: node['current'] == true
                          ? kPrimary.withValues(alpha: .5)
                          : kBorder,
                    ),
                  ),
                  child: Row(
                    children: [
                      Icon(
                        (node['parent_id'] == null)
                            ? Icons.account_tree_outlined
                            : Icons.call_split,
                        size: 16,
                        color: node['current'] == true ? kAccent : kMuted,
                      ),
                      const SizedBox(width: 9),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              (node['title'] ?? 'Campanha').toString(),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 12.5,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                            Text(
                              [
                                '${node['turn_count'] ?? 0} turnos',
                                if (node['branch_from'] != null)
                                  'ramo do turno ${node['branch_from']}',
                              ].join(' · '),
                              style: const TextStyle(
                                color: kMuted,
                                fontSize: 10,
                              ),
                            ),
                          ],
                        ),
                      ),
                      if (node['current'] == true)
                        const Text(
                          'atual',
                          style: TextStyle(
                            color: kAccent,
                            fontSize: 10,
                            fontWeight: FontWeight.w700,
                          ),
                        )
                      else
                        TextButton(
                          onPressed: () => open(node),
                          child: const Text('Abrir'),
                        ),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _showCodex() async {
    Map<String, dynamic> data;
    try {
      data = await ApiClient.codex(widget.session.id);
    } catch (e) {
      _snack(ApiClient.friendly(e));
      return;
    }
    if (!mounted) return;
    List<String> asList(Object? raw) {
      if (raw is List) {
        return raw.map((e) => e.toString()).where((e) => e.isNotEmpty).toList();
      }
      if (raw is String && raw.trim().startsWith('[')) {
        try {
          final decoded = jsonDecode(raw);
          if (decoded is List) {
            return decoded
                .map((e) => e.toString())
                .where((e) => e.isNotEmpty)
                .toList();
          }
        } catch (_) {}
      }
      return const [];
    }

    final people = ((data['people'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final locations = ((data['locations'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final worldFacts = ((data['world_facts'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final entityFacts = ((data['entity_facts'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final voices = ((data['voices'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final world = (data['world'] as Map?) ?? const {};

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .84,
        maxChildSize: .95,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 26),
          children: [
            const Text(
              'Códice',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            Text(
              (world['name'] ?? '').toString().isEmpty
                  ? 'O que esta história descobriu até agora.'
                  : '${world['name']} · o que esta história descobriu.',
              style: const TextStyle(color: kMuted, fontSize: 12),
            ),
            _sheetSection('Pessoas'),
            if (people.isEmpty)
              const Text(
                'Ninguém registrado ainda.',
                style: TextStyle(color: kMuted, fontSize: 12),
              ),
            for (final person in people)
              Container(
                margin: const EdgeInsets.only(bottom: 8),
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: kSurface2,
                  borderRadius: BorderRadius.circular(13),
                  border: Border.all(color: kBorder),
                ),
                child: Row(
                  children: [
                    LuminusAvatar(
                      radius: 17,
                      url: (person['avatar'] ?? '').toString(),
                      letter: (person['name'] ?? '?').toString().isEmpty
                          ? '?'
                          : (person['name'] ?? '?').toString()[0],
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            (person['name'] ?? '?').toString(),
                            style: const TextStyle(
                              fontSize: 13,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          if ((person['tagline'] ?? '').toString().isNotEmpty)
                            Text(
                              person['tagline'].toString(),
                              maxLines: 2,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                color: kMuted,
                                fontSize: 10.5,
                                height: 1.35,
                              ),
                            ),
                          Text(
                            [
                              (person['role'] ?? '').toString(),
                              if (((person['last_turn'] as num?)?.toInt() ??
                                      0) >
                                  0)
                                'visto no turno ${person['last_turn']}',
                            ].where((e) => e.isNotEmpty).join(' · '),
                            style: const TextStyle(
                              color: kMuted,
                              fontSize: 9.5,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            _sheetSection('Lugares'),
            if (locations.isEmpty)
              const Text(
                'Nenhum lugar registrado ainda.',
                style: TextStyle(color: kMuted, fontSize: 12),
              ),
            for (final location in locations)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    const Icon(Icons.place_outlined, size: 14, color: kMuted),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        (location['name'] ?? '').toString(),
                        style: const TextStyle(fontSize: 12.5),
                      ),
                    ),
                    Text(
                      'T${location['first_turn'] ?? 0}–${location['last_turn'] ?? 0}',
                      style: const TextStyle(color: kMuted, fontSize: 10),
                    ),
                  ],
                ),
              ),
            _sheetSection('Lore do mundo'),
            if (worldFacts.isEmpty)
              const Text(
                'Nenhuma lore registrada.',
                style: TextStyle(color: kMuted, fontSize: 12),
              ),
            for (final fact in worldFacts)
              Container(
                margin: const EdgeInsets.only(bottom: 7),
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: kSurface2,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: kBorder),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      (fact['text'] ?? '').toString(),
                      style: const TextStyle(fontSize: 12.5, height: 1.4),
                    ),
                    if ((fact['keys'] ?? '').toString().trim().isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 3),
                        child: Text(
                          'chaves: ${fact['keys']}',
                          style: const TextStyle(color: kMuted, fontSize: 10),
                        ),
                      ),
                  ],
                ),
              ),
            _sheetSection('Fatos por entidade'),
            if (entityFacts.isEmpty)
              const Text(
                'Nenhum fato por entidade.',
                style: TextStyle(color: kMuted, fontSize: 12),
              ),
            for (final fact in entityFacts)
              Padding(
                padding: const EdgeInsets.only(bottom: 7),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${fact['entity']}: ${fact['fact']}',
                      style: const TextStyle(fontSize: 12, height: 1.4),
                    ),
                    if (asList(fact['known_by']).isNotEmpty)
                      Text(
                        'sabem: ${asList(fact['known_by']).join(', ')}',
                        style: const TextStyle(color: kMuted, fontSize: 10),
                      ),
                  ],
                ),
              ),
            _sheetSection('Cartas e boatos'),
            if (voices.isEmpty)
              const Text(
                'Nenhuma carta registrada.',
                style: TextStyle(color: kMuted, fontSize: 12),
              ),
            for (final voice in voices)
              Container(
                margin: const EdgeInsets.only(bottom: 8),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: kSurface2,
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: kBorder),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      (voice['title'] ?? 'Artefato').toString(),
                      style: const TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    Text(
                      '${voice['author'] ?? 'autor desconhecido'} · '
                      '${_voiceTruthLabel((voice['truth_status'] ?? 'incerto').toString())}',
                      style: const TextStyle(color: kMuted, fontSize: 10),
                    ),
                    if ((voice['body'] ?? '').toString().trim().isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text(
                          voice['body'].toString(),
                          style: const TextStyle(fontSize: 11.5, height: 1.4),
                        ),
                      ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  Future<void> _showDirector() async {
    Map<String, dynamic> data;
    try {
      data = await ApiClient.director(widget.session.id);
    } catch (e) {
      _snack(ApiClient.friendly(e));
      return;
    }
    if (!mounted) return;
    final scene = (data['scene'] as Map?) ?? const {};
    final resolution = (data['resolution'] as Map?) ?? const {};
    final pacing = (data['pacing'] as Map?) ?? const {};
    final offscreen = (data['offscreen'] as List?) ?? const [];
    final metrics = (data['metrics'] as Map?) ?? const {};
    final counters = (metrics['counters'] as Map?) ?? const {};
    final timings = (metrics['timings'] as Map?) ?? const {};
    final pretty = const JsonEncoder.withIndent('  ').convert(data);
    final present = ((scene['present'] as List?) ?? const [])
        .map((e) => e.toString())
        .join(', ');

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .82,
        maxChildSize: .95,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 26),
          children: [
            const Text(
              'Painel do Diretor',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            const Text(
              'Leitura do estado interno da cena. Nada aqui altera a história.',
              style: TextStyle(color: kMuted, fontSize: 12, height: 1.4),
            ),
            _sheetSection('Cena'),
            _directorRow('Título', scene['title']),
            _directorRow('Local', scene['location']),
            _directorRow('Presentes', present),
            _directorRow('Ação', scene['action']),
            _directorRow('Ritmo', pacing['beat']),
            if (resolution.isNotEmpty) ...[
              _sheetSection('Última rolagem'),
              _directorRow('Teste', resolution['label']),
              _directorRow(
                'Resultado',
                'd20 ${resolution['roll']} '
                    '${((resolution['modifier'] as num?)?.toInt() ?? 0) >= 0 ? '+' : ''}'
                    '${resolution['modifier'] ?? 0} = ${resolution['total']} '
                    'vs CD ${resolution['dc']} — ${resolution['outcome_label']}',
              ),
            ],
            _sheetSection('Mundo'),
            _directorRow('Eventos offscreen', '${offscreen.length}'),
            _sheetSection('Métricas'),
            _directorRow(
              'Uptime',
              '${((metrics['uptime_seconds'] as num?)?.toDouble() ?? 0).toStringAsFixed(0)} s',
            ),
            _directorRow('Chats', '${counters['chat_requests'] ?? 0}'),
            _directorRow('Erros', '${counters['chat_errors'] ?? 0}'),
            _directorRow('Rolagens', '${counters['resolution_rolls'] ?? 0}'),
            if (timings['narration_seconds'] is Map)
              _directorRow(
                'Narração p95',
                '${(((timings['narration_seconds'] as Map)['p95'] as num?)?.toDouble() ?? 0).toStringAsFixed(1)} s',
              ),
            const SizedBox(height: 8),
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: const Text(
                'Estado bruto',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
              ),
              children: [
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: kBg,
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: kBorder),
                  ),
                  child: SelectableText(
                    pretty,
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      fontSize: 10,
                      height: 1.4,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: pretty));
                if (mounted) _snack('Estado copiado.');
              },
              icon: const Icon(Icons.copy_all_outlined, size: 18),
              label: const Text('Copiar estado JSON'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _directorRow(String label, Object? value) {
    final text = (value ?? '').toString().trim();
    if (text.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 118,
            child: Text(
              label,
              style: const TextStyle(color: kMuted, fontSize: 11.5),
            ),
          ),
          Expanded(
            child: Text(
              text,
              style: const TextStyle(fontSize: 12.5, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _maybeShowDigest() async {
    Map<String, dynamic> data;
    try {
      data = await ApiClient.digest(widget.session.id);
    } catch (_) {
      return;
    }
    final turn = (data['turn'] as num?)?.toInt() ?? 0;
    final offscreen = (data['offscreen'] as List?) ?? const [];
    final voices = (data['voices'] as List?) ?? const [];
    if (offscreen.isEmpty && voices.isEmpty) return;
    final prefs = await SharedPreferences.getInstance();
    final seen = prefs.getInt('digest_seen_${widget.session.id}') ?? 0;
    if (turn <= seen || !mounted) return;
    await _showDigest(data);
    await prefs.setInt('digest_seen_${widget.session.id}', turn);
  }

  Future<void> _openDigest() async {
    try {
      final data = await ApiClient.digest(widget.session.id);
      if (!mounted) return;
      await _showDigest(data);
    } catch (e) {
      _snack(ApiClient.friendly(e));
    }
  }

  Future<void> _showDigest(Map<String, dynamic> data) async {
    final offscreen = ((data['offscreen'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final voices = ((data['voices'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .72,
        maxChildSize: .95,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 26),
          children: [
            const Text(
              'Enquanto você estava fora',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            const Text(
              'O mundo continuou sem você. Nada disso é conhecimento automático do seu personagem.',
              style: TextStyle(color: kMuted, fontSize: 12, height: 1.45),
            ),
            if (offscreen.isEmpty && voices.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 28),
                child: Text(
                  'O mundo está em silêncio por enquanto.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: kMuted),
                ),
              ),
            if (offscreen.isNotEmpty) ...[
              _sheetSection('O mundo se moveu'),
              for (final event in offscreen)
                Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: kSurface2,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: kBorder),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        (event['text'] ?? '').toString(),
                        style: const TextStyle(fontSize: 13, height: 1.45),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'turno ${event['turn'] ?? 0} · ${event['visibility'] ?? 'rumor'}',
                        style: const TextStyle(color: kMuted, fontSize: 10.5),
                      ),
                    ],
                  ),
                ),
            ],
            if (voices.isNotEmpty) ...[
              _sheetSection('Cartas e boatos'),
              for (final voice in voices)
                Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding: const EdgeInsets.all(13),
                  decoration: BoxDecoration(
                    color: kSurface2,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: kBorder),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              (voice['title'] ?? 'Artefato').toString(),
                              style: const TextStyle(
                                fontSize: 13.5,
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                          Text(
                            _voiceKindLabel(
                              (voice['kind'] ?? 'rumor').toString(),
                            ),
                            style: const TextStyle(
                              color: kAccent,
                              fontSize: 9.5,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                        ],
                      ),
                      const SizedBox(height: 3),
                      Text(
                        'por ${(voice['author'] ?? 'autor desconhecido')} · '
                        '${_voiceTruthLabel((voice['truth_status'] ?? 'incerto').toString())}',
                        style: const TextStyle(color: kMuted, fontSize: 10.5),
                      ),
                      if ((voice['body'] ?? '')
                          .toString()
                          .trim()
                          .isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Text(
                          voice['body'].toString(),
                          style: const TextStyle(fontSize: 12.5, height: 1.45),
                        ),
                      ],
                    ],
                  ),
                ),
            ],
            const SizedBox(height: 8),
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Continuar'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _showJournal() async {
    Map<String, dynamic> data;
    try {
      data = await ApiClient.threads(widget.session.id);
    } catch (e) {
      _snack(ApiClient.friendly(e));
      return;
    }
    if (!mounted) return;
    final threads = ((data['threads'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final stats = (data['stats'] as Map?) ?? const {};
    final turn = (data['turn'] as num?)?.toInt() ?? 0;

    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .82,
        maxChildSize: .95,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 28),
          children: [
            const Text(
              'Diário de fios',
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            const Text(
              'Promessas, dívidas, mistérios e objetivos em aberto.',
              style: TextStyle(color: kMuted, fontSize: 12, height: 1.4),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                _journalStat('Abertos', stats['open']),
                _journalStat('Resolvidos', stats['resolved']),
                _journalStat('Vencidos', stats['overdue'], danger: true),
                _journalStat(
                  'Sobrevivência',
                  '${((stats['survival'] as num?)?.toDouble() ?? 1) * 100 ~/ 1}%',
                ),
              ],
            ),
            const Divider(height: 26),
            if (threads.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 24),
                child: Text(
                  'Nenhum fio registrado ainda. O diretor cria fios quando uma promessa, dívida ou mistério aparece.',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: kMuted, height: 1.5),
                ),
              ),
            for (final thread in threads) _journalCard(thread, turn),
          ],
        ),
      ),
    );
  }

  Widget _journalStat(String label, Object? value, {bool danger = false}) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
        color: kSurface2,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: danger ? kDanger.withValues(alpha: .45) : kBorder,
        ),
      ),
      child: Text(
        '$label ${value ?? 0}',
        style: TextStyle(
          color: danger ? kDanger : kMuted,
          fontSize: 10.5,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }

  Widget _journalCard(Map<String, dynamic> thread, int turn) {
    final status = (thread['status'] ?? 'aberta').toString();
    final open = status == 'aberta';
    final due = (thread['due_turn'] as num?)?.toInt() ?? 0;
    final overdue = open && due > 0 && due <= turn;
    final color = overdue
        ? kDanger
        : status == 'resolvida'
        ? kPrimary
        : status == 'abandonada'
        ? kMuted
        : kAccent;
    final setup = (thread['setup'] ?? '').toString().trim();
    final payoff = (thread['payoff'] ?? '').toString().trim();
    final entities = ((thread['entities'] as List?) ?? const [])
        .map((e) => e.toString())
        .where((e) => e.isNotEmpty)
        .toList();
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: kSurface2,
        borderRadius: BorderRadius.circular(15),
        border: Border.all(color: color.withValues(alpha: .35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: .13),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Text(
                  (thread['kind'] ?? 'fio').toString(),
                  style: TextStyle(
                    color: color,
                    fontSize: 9.5,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              const Spacer(),
              Text(
                overdue
                    ? 'vencido no turno $due'
                    : due > 0
                    ? 'prazo: turno $due'
                    : statusLabel(status),
                style: TextStyle(
                  color: color,
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            (thread['title'] ?? 'Fio').toString(),
            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w700),
          ),
          if (setup.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              setup,
              maxLines: 3,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: kMuted,
                fontSize: 11.5,
                height: 1.4,
              ),
            ),
          ],
          if (payoff.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              'Payoff: $payoff',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: kMuted,
                fontSize: 11,
                fontStyle: FontStyle.italic,
                height: 1.4,
              ),
            ),
          ],
          if (entities.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(
              'Envolve: ${entities.join(', ')}',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(color: kMuted, fontSize: 10.5),
            ),
          ],
        ],
      ),
    );
  }

  String statusLabel(String status) {
    switch (status) {
      case 'aberta':
        return 'aberto';
      case 'resolvida':
        return 'resolvido';
      case 'abandonada':
        return 'abandonado';
      default:
        return status;
    }
  }

  Future<void> _showSearch() async {
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => _SearchSheet(
        sessionId: widget.session.id,
        characterName: widget.character.name,
      ),
    );
  }

  Future<void> _exportChronicle() async {
    try {
      final data = await ApiClient.chronicle(widget.session.id);
      final markdown = (data['markdown'] ?? '').toString();
      if (markdown.trim().isEmpty) throw 'A crônica está vazia.';
      final title = (data['title'] ?? _sessionTitle).toString();
      final slug = title
          .toLowerCase()
          .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
          .replaceAll(RegExp(r'^-+|-+$'), '');
      final now = DateTime.now();
      final stamp =
          '${now.year}'
          '${now.month.toString().padLeft(2, '0')}'
          '${now.day.toString().padLeft(2, '0')}-'
          '${now.hour.toString().padLeft(2, '0')}'
          '${now.minute.toString().padLeft(2, '0')}';
      final name = 'luminus-${slug.isEmpty ? 'cronica' : slug}-$stamp.md';
      final uri = await LocalFiles.exportText(
        markdown,
        suggestedName: name,
        mime: 'text/plain',
      );
      if (!mounted) return;
      _snack(uri == null ? 'Exportação cancelada.' : 'Crônica exportada.');
    } catch (e) {
      _snack(e is String ? e : ApiClient.friendly(e));
    }
  }

  Future<void> _showBacklog() async {
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .82,
        maxChildSize: .95,
        builder: (_, controller) => ListView.builder(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 28),
          itemCount: _msgs.length + 1,
          itemBuilder: (_, i) {
            if (i == 0) {
              return const Padding(
                padding: EdgeInsets.only(bottom: 16),
                child: Text(
                  'Backlog',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
                ),
              );
            }
            final m = _msgs[i - 1];
            final isUser = m.role == 'user';
            return Padding(
              padding: const EdgeInsets.only(bottom: 14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    isUser ? 'VOCÊ' : widget.character.name.toUpperCase(),
                    style: TextStyle(
                      color: isUser ? kPrimary : kAccent,
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 1.1,
                    ),
                  ),
                  const SizedBox(height: 4),
                  if (isUser)
                    Text(
                      m.content,
                      style: const TextStyle(
                        fontFamily: kSans,
                        fontSize: 13.5,
                        height: 1.5,
                      ),
                    )
                  else
                    StoryText(
                      text: m.content,
                      style: storyStyle(size: 14.5 * _textScale, height: 1.55),
                    ),
                  const Divider(height: 22),
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  Future<void> _showMemory() async {
    try {
      final m = await ApiClient.memory(widget.session.id);
      final recap = (m['recap'] ?? '') as String;
      final chapters = ((m['chapters'] as List?) ?? const [])
          .map((e) => Chapter.fromJson(e as Map<String, dynamic>))
          .toList();
      if (!mounted) return;
      showModalBottomSheet(
        context: context,
        isScrollControlled: true,
        backgroundColor: kSurface,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
        ),
        builder: (_) => DraggableScrollableSheet(
          expand: false,
          initialChildSize: .72,
          maxChildSize: .95,
          builder: (_, controller) => ListView(
            controller: controller,
            padding: const EdgeInsets.fromLTRB(18, 14, 18, 24),
            children: [
              const Text(
                'Memória',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              const Text(
                'O que o mundo lembra desta história.',
                style: TextStyle(color: kMuted, fontSize: 12),
              ),
              const SizedBox(height: 16),
              if (recap.isNotEmpty) ...[
                const Text(
                  'Recap',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 6),
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: kSurface2,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: kBorder),
                  ),
                  child: Text(
                    recap,
                    style: const TextStyle(height: 1.5, fontSize: 13.5),
                  ),
                ),
                const SizedBox(height: 18),
              ],
              if (chapters.isNotEmpty)
                const Text(
                  'Capítulos',
                  style: TextStyle(fontWeight: FontWeight.w700),
                ),
              for (final c in chapters) ...[
                const SizedBox(height: 10),
                Text(
                  'Capítulo ${c.number}',
                  style: const TextStyle(
                    color: kAccent,
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  c.summary,
                  style: const TextStyle(height: 1.45, fontSize: 13.5),
                ),
                const Divider(height: 22),
              ],
              if (recap.isEmpty && chapters.isEmpty)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 20),
                  child: Text(
                    'Sem memória ainda. Jogue alguns turnos.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: kMuted),
                  ),
                ),
            ],
          ),
        ),
      );
    } catch (e) {
      _snack(ApiClient.friendly(e));
    }
  }

  Future<void> _reset() async {
    final ok = await _confirm(
      'Reiniciar história?',
      'Apaga o histórico e a memória deste save. Essa ação não pode ser desfeita.',
    );
    if (ok) {
      await ApiClient.resetSession(widget.session.id);
      _load();
    }
  }

  Future<void> _rewindAt(_Msg m) async {
    if (_sending || m.id <= 0) return;
    final ok = await _confirm(
      'Rebobinar até aqui?',
      'As mensagens seguintes serão apagadas e o estado volta a este ponto.',
    );
    if (!ok) return;
    try {
      await ApiClient.rewindSession(widget.session.id, m.id);
      if (!mounted) return;
      await _load();
      _snack('História rebobinada.');
    } catch (e) {
      _snack(ApiClient.friendly(e));
    }
  }

  Future<void> _branchAt(_Msg m) async {
    final ok = await _confirm(
      'Criar linha alternativa?',
      'Uma nova história será criada a partir deste ponto. A atual continua salva.',
    );
    if (!ok) return;
    try {
      final s = await ApiClient.branchSession(
        widget.session.id,
        messageId: m.id > 0 ? m.id : null,
      );
      if (!mounted) return;
      Navigator.pushReplacement(
        context,
        MaterialPageRoute(
          builder: (_) => ChatScreen(character: widget.character, session: s),
        ),
      );
    } catch (e) {
      _snack(ApiClient.friendly(e));
    }
  }

  Widget _mini(IconData icon, VoidCallback onTap, String tip) {
    return Tooltip(
      message: tip,
      child: InkWell(
        borderRadius: BorderRadius.circular(22),
        onTap: onTap,
        child: SizedBox(
          width: 44,
          height: 44,
          child: Center(child: Icon(icon, size: 18, color: kMuted)),
        ),
      ),
    );
  }

  Widget _quickPill(
    IconData icon,
    String label,
    VoidCallback onTap, {
    bool active = false,
  }) {
    final color = active ? kAccent : kMuted;
    return Material(
      color: active
          ? kAccent.withValues(alpha: .12)
          : kSurface.withValues(alpha: .72),
      borderRadius: BorderRadius.circular(20),
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 38),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(20),
            border: Border.all(
              color: active ? kAccent.withValues(alpha: .42) : kBorder,
            ),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 15, color: color),
              const SizedBox(width: 5),
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
        ),
      ),
    );
  }

  String _statusText(String? status) {
    switch (status) {
      case 'planning':
        return 'Preparando a cena...';
      case 'rolling':
        return 'Consultando o destino...';
      case 'writing':
        return 'Escrevendo...';
      case 'error':
        return '';
      default:
        return '';
    }
  }

  Widget _bubbleBody(_Msg m, bool isUser) {
    final status = _statusText(m.status);
    final radius = BorderRadius.only(
      topLeft: Radius.circular(isUser ? 20 : 6),
      topRight: Radius.circular(isUser ? 6 : 20),
      bottomLeft: const Radius.circular(20),
      bottomRight: const Radius.circular(20),
    );
    return GestureDetector(
      onLongPress: _sending ? null : () => _showActions(m),
      child: Container(
        constraints: BoxConstraints(
          maxWidth: MediaQuery.sizeOf(context).width * .84,
        ),
        padding: const EdgeInsets.fromLTRB(15, 12, 15, 13),
        decoration: BoxDecoration(
          gradient: isUser
              ? const LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [kPrimaryDeep, kPrimary],
                )
              : null,
          color: isUser ? null : kSurface2.withValues(alpha: .62),
          border: isUser ? null : Border.all(color: kBorder),
          borderRadius: radius,
        ),
        child: m.streaming && m.content.isEmpty
            ? Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const _TypingDots(),
                  if (status.isNotEmpty) ...[
                    const SizedBox(width: 10),
                    Text(
                      status,
                      style: const TextStyle(
                        color: kMuted,
                        fontSize: 12.5,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ],
              )
            : StoryText(
                text: m.content,
                streaming: m.streaming,
                style: isUser
                    ? TextStyle(
                        fontFamily: kSans,
                        color: Colors.white,
                        height: 1.48,
                        fontSize: 15.2 * _textScale,
                      )
                    : storyStyle(size: 16.1 * _textScale, height: 1.62),
              ),
      ),
    );
  }

  Widget _message(_Msg m) {
    final isUser = m.role == 'user';
    final c = widget.character;
    if (isUser) {
      return Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Align(
          alignment: Alignment.centerRight,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              _bubbleBody(m, true),
              if (!m.streaming && m.content.trim().isNotEmpty)
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    _mini(Icons.edit_outlined, () => _edit(m), 'Editar'),
                    _mini(Icons.copy_all_outlined, () => _copy(m), 'Copiar'),
                    _mini(Icons.more_horiz, () => _showActions(m), 'Mais'),
                  ],
                ),
            ],
          ),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          LuminusAvatar(
            radius: 15,
            url: c.avatar,
            letter: c.name.isNotEmpty ? c.name[0] : '?',
          ),
          const SizedBox(width: 8),
          Flexible(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  c.name,
                  style: const TextStyle(
                    color: kMuted,
                    fontSize: 11.5,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 4),
                _bubbleBody(m, false),
                if (m.resolution != null) ...[
                  const SizedBox(height: 6),
                  _diceReceipt(m.resolution!),
                ],
                if (!m.streaming && m.content.trim().isNotEmpty)
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      _mini(
                        Icons.volume_up_outlined,
                        () => _speak(m.content),
                        'Ouvir',
                      ),
                      _mini(Icons.copy_all_outlined, () => _copy(m), 'Copiar'),
                      if (_canRegen(m))
                        _mini(Icons.refresh, () => _regenerate(m), 'Regenerar'),
                      _mini(Icons.more_horiz, () => _showActions(m), 'Mais'),
                    ],
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _backdrop(String url) {
    if (url.trim().isEmpty) return const SizedBox.shrink();
    final isScene = _sceneImage.trim().isNotEmpty;
    return Positioned.fill(
      child: IgnorePointer(
        child: Stack(
          fit: StackFit.expand,
          children: [
            ImageFiltered(
              imageFilter: ImageFilter.blur(
                sigmaX: isScene ? 16 : 26,
                sigmaY: isScene ? 16 : 26,
              ),
              child: Image.network(
                ApiClient.mediaUrl(url),
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) => const SizedBox.shrink(),
              ),
            ),
            DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    kBg.withValues(alpha: .74),
                    kBg.withValues(alpha: .9),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _sceneHeader() {
    final sceneTitle = (_sceneState?['title'] ?? '').toString().trim();
    final sceneLocation = (_sceneState?['location'] ?? '').toString().trim();
    final title = sceneTitle.isNotEmpty
        ? sceneTitle
        : (_sessionTitle.trim().isEmpty ? 'Narração' : _sessionTitle);
    final world = _worldName.isNotEmpty
        ? _worldName
        : (sceneLocation.isNotEmpty ? sceneLocation : 'Narração');
    final chapter = _chapterCount > 0 ? 'Capítulo $_chapterCount' : 'Prólogo';
    return Container(
      margin: const EdgeInsets.only(bottom: 18),
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF241A5A), Color(0xFF121026)],
        ),
        border: Border.all(color: kBorder),
      ),
      child: Stack(
        children: [
          if (_sceneImage.trim().isNotEmpty)
            Positioned.fill(
              child: Opacity(
                opacity: .5,
                child: Image.network(
                  ApiClient.mediaUrl(_sceneImage),
                  fit: BoxFit.cover,
                  errorBuilder: (_, _, _) => const SizedBox.shrink(),
                ),
              ),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 15),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Icon(Icons.auto_stories, size: 15, color: kAccent),
                    const SizedBox(width: 7),
                    Expanded(
                      child: Text(
                        world.toUpperCase(),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: kAccent,
                          fontSize: 10.5,
                          fontWeight: FontWeight.w700,
                          letterSpacing: 1.2,
                        ),
                      ),
                    ),
                    Text(
                      chapter,
                      style: const TextStyle(
                        color: kMuted,
                        fontSize: 10.5,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(width: 2),
                    IconButton(
                      visualDensity: VisualDensity.compact,
                      tooltip: _generatingScene
                          ? 'Pintando...'
                          : (_sceneImage.isEmpty
                                ? 'Ilustrar cena'
                                : 'Gerar outra cena'),
                      onPressed: _generatingScene ? null : _generateScene,
                      icon: _generatingScene
                          ? const SizedBox(
                              width: 15,
                              height: 15,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Icon(
                              _sceneImage.isEmpty
                                  ? Icons.image_outlined
                                  : Icons.auto_awesome,
                              size: 17,
                              color: kAccent,
                            ),
                    ),
                  ],
                ),
                const SizedBox(height: 9),
                Text(
                  title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 15.5,
                    fontWeight: FontWeight.w700,
                    height: 1.3,
                  ),
                ),
                if (_protagonist != null) ...[
                  const SizedBox(height: 5),
                  Row(
                    children: [
                      const Icon(Icons.person_outline, size: 13, color: kMuted),
                      const SizedBox(width: 5),
                      Expanded(
                        child: Text(
                          'Você interpreta ${_protagonist!.name}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: kMuted,
                            fontSize: 11.5,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
                if (widget.session.summary.trim().isNotEmpty) ...[
                  const SizedBox(height: 5),
                  Text(
                    widget.session.summary,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: kMuted,
                      fontSize: 11.5,
                      height: 1.4,
                    ),
                  ),
                ],
                if (_vinculo.isNotEmpty) ...[
                  const SizedBox(height: 7),
                  Text(
                    '"$_vinculo"',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: kPrimary,
                      fontSize: 11,
                      fontStyle: FontStyle.italic,
                    ),
                  ),
                ],
                if (_cast.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      for (final m in _cast.take(5))
                        Padding(
                          padding: const EdgeInsets.only(right: 6),
                          child: Tooltip(
                            message: '${m.character.name} · NR ${m.nr}',
                            child: LuminusAvatar(
                              radius: 11,
                              url: m.character.avatar,
                              letter: m.character.name.isNotEmpty
                                  ? m.character.name[0]
                                  : '?',
                            ),
                          ),
                        ),
                      const SizedBox(width: 4),
                      Expanded(
                        child: Text(
                          '${(_cast.isNotEmpty ? _cast.length : (_castFromDone?.length ?? 0)) + 1} NPCs na história',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(color: kMuted, fontSize: 10.5),
                        ),
                      ),
                    ],
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _openingSplash() {
    final c = widget.character;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(28, 24, 28, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            LuminusAvatar(
              radius: 38,
              url: c.avatar,
              letter: c.name.isNotEmpty ? c.name[0] : '?',
              ring: true,
            ),
            const SizedBox(height: 18),
            const Text(
              'Abrindo o mundo',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 6),
            Text(
              'A primeira cena com ${c.name} está sendo escrita para você.',
              textAlign: TextAlign.center,
              style: const TextStyle(color: kMuted, fontSize: 13, height: 1.5),
            ),
            const SizedBox(height: 22),
            const SizedBox(
              width: 170,
              child: LinearProgressIndicator(minHeight: 4),
            ),
            const SizedBox(height: 12),
            const Text(
              'O narrador pode levar até 2 minutos na primeira vez.',
              textAlign: TextAlign.center,
              style: TextStyle(color: kMuted, fontSize: 11, height: 1.4),
            ),
          ],
        ),
      ),
    );
  }

  Widget _emptyChat() {
    final c = widget.character;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.auto_awesome, size: 36, color: kAccent),
            const SizedBox(height: 12),
            const Text(
              'A cena ainda não começou',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 6),
            Text(
              'Deixe a IA escrever a abertura e apresentar ${c.name} a você.',
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: kMuted,
                fontSize: 12.5,
                height: 1.45,
              ),
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: _ensureOpening,
              icon: const Icon(Icons.play_arrow_rounded),
              label: const Text('Abrir a cena'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _openingFailedBanner() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 4, 14, 2),
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 6, 6, 6),
        decoration: BoxDecoration(
          color: kDanger.withValues(alpha: .12),
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: kDanger.withValues(alpha: .35)),
        ),
        child: Row(
          children: [
            const Icon(Icons.cloud_off, size: 16, color: kDanger),
            const SizedBox(width: 8),
            const Expanded(
              child: Text(
                'A abertura não foi escrita pela IA.',
                style: TextStyle(fontSize: 11.5, height: 1.35),
              ),
            ),
            TextButton(onPressed: _ensureOpening, child: const Text('Tentar')),
          ],
        ),
      ),
    );
  }

  _Msg? _lastAssistant() {
    for (var i = _msgs.length - 1; i >= 0; i--) {
      final m = _msgs[i];
      if (m.role == 'assistant' && m.content.trim().isNotEmpty) return m;
    }
    return null;
  }

  Widget _memoryStrip() {
    final parts = <String>[
      if (_worldName.isNotEmpty) _worldName,
      'NR $_nr',
      if (_vinculo.isNotEmpty) _vinculo,
      if (_chapterCount > 0) 'Cap. $_chapterCount',
    ];
    final openThreads = (_commitments?['open'] as num?)?.toInt() ?? 0;
    if (openThreads > 0) parts.add('Fios $openThreads');
    final beat = (_pacing?['beat'] ?? '').toString().trim();
    if (beat.isNotEmpty) parts.add(beat);
    final outcome = (_resolution?['outcome_label'] ?? '').toString().trim();
    if (outcome.isNotEmpty) parts.add(outcome);
    if (parts.isEmpty) return const SizedBox.shrink();
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: kBg.withValues(alpha: .68),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: kBorder),
      ),
      child: Row(
        children: [
          const GameIcon('compass', size: 16, color: kAccent),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              parts.join('  ·  '),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(
                color: kMuted,
                fontSize: 11.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _readerStage() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_openingLoading) return _openingSplash();
    if (_msgs.isEmpty) return _emptyChat();
    return Stack(
      fit: StackFit.expand,
      children: [
        _backdrop(
          _sceneImage.trim().isNotEmpty ? _sceneImage : widget.character.avatar,
        ),
        ListView.builder(
          controller: _scroll,
          padding: const EdgeInsets.fromLTRB(20, 18, 20, 30),
          itemCount: _msgs.length + 1,
          itemBuilder: (_, i) {
            if (i == 0) return _sceneHeader();
            return _readerMessage(_msgs[i - 1]);
          },
        ),
      ],
    );
  }

  Widget _readerMessage(_Msg m) {
    final isUser = m.role == 'user';
    if (isUser) {
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          margin: const EdgeInsets.only(bottom: 20, left: 44),
          padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
          decoration: BoxDecoration(
            color: kSurface.withValues(alpha: .48),
            border: const Border(left: BorderSide(color: kPrimary, width: 2)),
            borderRadius: BorderRadius.circular(9),
          ),
          child: Text(
            m.content,
            style: const TextStyle(
              fontFamily: kSans,
              color: kText,
              fontSize: 13.5,
              height: 1.45,
            ),
          ),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            widget.character.name.toUpperCase(),
            style: const TextStyle(
              color: kAccent,
              fontSize: 10.5,
              fontWeight: FontWeight.w700,
              letterSpacing: 1.2,
            ),
          ),
          const SizedBox(height: 6),
          StoryText(
            text: m.content,
            streaming: m.streaming,
            style: storyStyle(size: 16.5 * _textScale, height: 1.66),
          ),
          if (m.resolution != null) ...[
            const SizedBox(height: 10),
            _diceReceipt(m.resolution!),
          ],
        ],
      ),
    );
  }

  Widget _diceReceipt(Map<String, dynamic> receipt) {
    final label = (receipt['label'] ?? 'teste').toString();
    final roll = (receipt['roll'] as num?)?.toInt() ?? 0;
    final modifier = (receipt['modifier'] as num?)?.toInt() ?? 0;
    final total = (receipt['total'] as num?)?.toInt() ?? 0;
    final dc = (receipt['dc'] as num?)?.toInt() ?? 0;
    final outcome = (receipt['outcome'] ?? '').toString();
    final outcomeLabel = (receipt['outcome_label'] ?? '').toString();
    final attribute = (receipt['attribute'] ?? '').toString();
    final attributeValue = (receipt['attribute_value'] as num?)?.toInt() ?? 0;
    final attributeMod = (receipt['attribute_mod'] as num?)?.toInt() ?? 0;
    final skill = (receipt['skill'] ?? '').toString();
    final skillLevel = (receipt['skill_level'] as num?)?.toInt() ?? 0;
    final skillMod = (receipt['skill_mod'] as num?)?.toInt() ?? 0;
    final luckMod = (receipt['luck_mod'] as num?)?.toInt() ?? 0;
    final situational = (receipt['situational'] as num?)?.toInt() ?? 0;
    final parts = ((receipt['situational_parts'] as List?) ?? const [])
        .whereType<Map>()
        .map((e) => e.map((k, v) => MapEntry(k.toString(), v)))
        .toList();
    final sources = parts
        .map(
          (part) =>
              '${part['source']} ${_signed((part['value'] as num?)?.toInt() ?? 0)}',
        )
        .join(' · ');
    final formulaParts = <String>[];
    if (attribute.isNotEmpty) {
      formulaParts.add(
        '+ ${_attributeLabel(attribute)} $attributeValue (${_signed(attributeMod)})',
      );
      if (skill.isNotEmpty) {
        formulaParts.add(
          '+ ${_skillLabel(skill)} $skillLevel (${_signed(skillMod)})',
        );
      }
      formulaParts.add('+ sorte (${_signed(luckMod)})');
      if (situational != 0) {
        formulaParts.add('+ situação (${_signed(situational)})');
      }
    }
    final formula = formulaParts.isEmpty
        ? 'd20 $roll ${_signed(modifier)} = $total vs CD $dc'
        : 'd20 $roll ${formulaParts.join(' ')} = $total vs CD $dc';
    final color = outcome == 'falha' || outcome == 'falha_critica'
        ? kDanger
        : outcome == 'parcial'
        ? kPrimary
        : kAccent;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: kSurface2.withValues(alpha: .58),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: .35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            crossAxisAlignment: WrapCrossAlignment.center,
            spacing: 8,
            runSpacing: 4,
            children: [
              Icon(Icons.casino_outlined, size: 15, color: color),
              Text(
                '$label · $formula',
                style: const TextStyle(color: kMuted, fontSize: 11),
              ),
              Text(
                outcomeLabel,
                style: TextStyle(
                  color: color,
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
          if (sources.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              sources,
              style: const TextStyle(color: kMuted, fontSize: 10),
            ),
          ],
        ],
      ),
    );
  }

  Set<String> get _presentNames {
    final raw = _sceneState?['present'];
    if (raw is List) {
      return raw
          .map((e) => e.toString().trim().toLowerCase())
          .where((e) => e.isNotEmpty)
          .toSet();
    }
    return const {};
  }

  bool _isPresent(String name) {
    final present = _presentNames;
    if (present.isEmpty) return true;
    return present.contains(name.trim().toLowerCase());
  }

  Widget _partyStrip() {
    if (_msgs.isEmpty || _loading || _openingLoading) {
      return const SizedBox.shrink();
    }
    final c = widget.character;
    final items = <Widget>[
      _partyAvatar(
        name: c.name,
        avatar: c.avatar,
        nr: _nr,
        present: _isPresent(c.name),
        onTap: _showRelationship,
      ),
      for (final m in _cast)
        _partyAvatar(
          name: m.character.name,
          avatar: m.character.avatar,
          nr: m.nr,
          present: _isPresent(m.character.name),
          onTap: () => _showCastMember(m),
        ),
    ];
    return Container(
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 4),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        color: kSurface.withValues(alpha: .55),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: kBorder),
      ),
      child: SizedBox(
        height: 62,
        child: ListView.separated(
          scrollDirection: Axis.horizontal,
          itemCount: items.length,
          separatorBuilder: (_, _) => const SizedBox(width: 10),
          itemBuilder: (_, i) => items[i],
        ),
      ),
    );
  }

  Widget _partyAvatar({
    required String name,
    required String avatar,
    required int nr,
    required bool present,
    required VoidCallback onTap,
  }) {
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: onTap,
      child: Opacity(
        opacity: present ? 1 : .45,
        child: SizedBox(
          width: 62,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Stack(
                clipBehavior: Clip.none,
                children: [
                  LuminusAvatar(
                    radius: 17,
                    url: avatar,
                    letter: name.isNotEmpty ? name[0] : '?',
                    ring: present,
                  ),
                  if (!present)
                    Positioned(
                      right: -2,
                      bottom: -2,
                      child: Container(
                        width: 12,
                        height: 12,
                        decoration: BoxDecoration(
                          color: kSurface2,
                          shape: BoxShape.circle,
                          border: Border.all(color: kBorder),
                        ),
                        child: const Icon(Icons.remove, size: 8, color: kMuted),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 3),
              Text(
                name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontSize: 9.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
              Text(
                'NR $nr',
                style: const TextStyle(
                  fontSize: 9,
                  color: kAccent,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _showCastMember(CastEntry m) async {
    const labels = {
      'trust': 'Confiança',
      'intimacy': 'Intimidade',
      'respect': 'Respeito',
      'tension': 'Tensão',
    };
    final c = m.character;
    await showModalBottomSheet(
      context: context,
      backgroundColor: kSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(20, 16, 20, 28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  LuminusAvatar(
                    radius: 26,
                    url: c.avatar,
                    letter: c.name.isNotEmpty ? c.name[0] : '?',
                    ring: true,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          c.name,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                        if (c.tagline.isNotEmpty)
                          Text(
                            c.tagline,
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
                  Text(
                    'NR ${m.nr}',
                    style: const TextStyle(
                      color: kAccent,
                      fontSize: 15,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Text(
                _nrTier(m.nr),
                style: const TextStyle(
                  color: kText,
                  fontSize: 12.5,
                  height: 1.4,
                ),
              ),
              if (m.vinculo.isNotEmpty) ...[
                const SizedBox(height: 6),
                Text(
                  '"${m.vinculo}"',
                  style: const TextStyle(
                    color: kMuted,
                    fontSize: 12,
                    fontStyle: FontStyle.italic,
                  ),
                ),
              ],
              const Divider(height: 26),
              for (final entry in labels.entries) ...[
                Row(
                  children: [
                    Text(
                      entry.value,
                      style: const TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const Spacer(),
                    Text(
                      '${m.axes[entry.key] ?? 0}',
                      style: const TextStyle(color: kMuted, fontSize: 11.5),
                    ),
                  ],
                ),
                const SizedBox(height: 5),
                ClipRRect(
                  borderRadius: BorderRadius.circular(6),
                  child: LinearProgressIndicator(
                    value: (((m.axes[entry.key] ?? 0) / 100).clamp(
                      0.0,
                      1.0,
                    )).toDouble(),
                    minHeight: 6,
                    backgroundColor: kSurface2,
                    color: entry.key == 'tension' ? kDanger : kPrimary,
                  ),
                ),
                const SizedBox(height: 12),
              ],
              if (m.mood.isNotEmpty) _castDetail('Humor', m.mood),
              if (m.goal.isNotEmpty) _castDetail('Objetivo', m.goal),
              if (m.status.isNotEmpty) _castDetail('Na cena', m.status),
            ],
          ),
        ),
      ),
    );
  }

  Widget _castDetail(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 70,
            child: Text(
              label,
              style: const TextStyle(color: kMuted, fontSize: 11.5),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontSize: 12.5, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }

  String? _portraitFor(Character c) {
    if (c.portraits.isEmpty) return null;
    final mood = _mood.toLowerCase();
    var key = 'neutral';
    if (mood.contains('tens') ||
        mood.contains('raiv') ||
        mood.contains('medo') ||
        mood.contains('irrit') ||
        mood.contains('hostil') ||
        mood.contains('frio') ||
        mood.contains('desconf')) {
      key = 'tense';
    } else if (mood.contains('sorr') ||
        mood.contains('alegr') ||
        mood.contains('feliz') ||
        mood.contains('carinho') ||
        mood.contains('calor') ||
        mood.contains('afetu')) {
      key = 'smile';
    }
    return c.portraits[key] ?? c.portraits['neutral'];
  }

  Widget _vnStage() {
    if (_loading) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_openingLoading) return _openingSplash();
    if (_msgs.isEmpty) return _emptyChat();
    final last = _lastAssistant();
    final c = widget.character;
    final portrait = _portraitFor(c);
    return Stack(
      fit: StackFit.expand,
      children: [
        _backdrop(_sceneImage.trim().isNotEmpty ? _sceneImage : c.avatar),
        const Positioned.fill(child: EmberField(count: 16)),
        Positioned(top: 10, left: 12, right: 12, child: _memoryStrip()),
        if (portrait != null)
          Positioned(
            right: 8,
            bottom: 200,
            child: IgnorePointer(
              child: ClipRRect(
                borderRadius: BorderRadius.circular(18),
                child: Image.network(
                  ApiClient.mediaUrl(portrait),
                  height: 310,
                  fit: BoxFit.contain,
                  errorBuilder: (_, _, _) => const SizedBox.shrink(),
                ),
              ),
            ),
          ),
        Positioned(
          left: 12,
          right: 12,
          bottom: 12,
          child: OrnateFrame(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  children: [
                    LuminusAvatar(
                      radius: 12,
                      url: c.avatar,
                      letter: c.name.isNotEmpty ? c.name[0] : '?',
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        c.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 12.5,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    if (last?.streaming == true) const _TypingDots(),
                  ],
                ),
                const SizedBox(height: 10),
                ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 216),
                  child: SingleChildScrollView(
                    child: StoryText(
                      text: (last?.content.trim().isNotEmpty ?? false)
                          ? last!.content
                          : 'A cena está em silêncio. Continue para ver o que acontece.',
                      style: storyStyle(size: 15.5 * _textScale, height: 1.6),
                      streaming: last?.streaming ?? false,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _choicesBar() {
    if (_sending ||
        _chaptering ||
        _openingLoading ||
        _openingFailed ||
        _generatingScene) {
      return const SizedBox.shrink();
    }
    if (_loadingChoices) {
      return Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
        child: Row(
          children: [
            const SizedBox(
              width: 12,
              height: 12,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
            const SizedBox(width: 8),
            const Text(
              'Gerando sugestões...',
              style: TextStyle(color: kMuted, fontSize: 11.5),
            ),
          ],
        ),
      );
    }
    if (_choices.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 6, 10, 4),
          child: Row(
            children: [
              const Text(
                'Sugestões',
                style: TextStyle(
                  color: kMuted,
                  fontSize: 11.5,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const Spacer(),
              InkWell(
                onTap: _loadChoices,
                borderRadius: BorderRadius.circular(16),
                child: const Padding(
                  padding: EdgeInsets.all(6),
                  child: Icon(Icons.refresh, size: 15, color: kMuted),
                ),
              ),
            ],
          ),
        ),
        for (var i = 0; i < _choices.length && i < 3; i++)
          _choiceCard(_choices[i], i),
        const SizedBox(height: 2),
      ],
    );
  }

  String _attributeLabel(String key) {
    switch (key) {
      case 'corpo':
        return 'Corpo';
      case 'mente':
        return 'Mente';
      case 'presenca':
        return 'Presença';
      case 'sorte':
        return 'Sorte';
      default:
        return key;
    }
  }

  String _skillLabel(String key) {
    switch (key) {
      case 'luta':
        return 'Luta';
      case 'atletismo':
        return 'Atletismo';
      case 'vigor':
        return 'Vigor';
      case 'furtividade':
        return 'Furtividade';
      case 'investigacao':
        return 'Investigação';
      case 'persuasao':
        return 'Persuasão';
      default:
        return key;
    }
  }

  String _intentLabel(String intent) {
    switch (intent) {
      case 'combate':
        return 'Combate';
      case 'furtividade':
        return 'Furtividade';
      case 'social':
        return 'Social';
      case 'investigacao':
        return 'Investigação';
      case 'movimento':
        return 'Movimento';
      case 'resistencia':
        return 'Resistência';
      default:
        return intent;
    }
  }

  String _modifierLabel(String intent, Map value) {
    final base = (value['modifier'] as num?)?.toInt() ?? 0;
    final situational = (value['situational'] as num?)?.toInt() ?? 0;
    final total = (value['total'] as num?)?.toInt() ?? base;
    if (situational == 0) {
      return '${_intentLabel(intent)} ${_signed(total)}';
    }
    return '${_intentLabel(intent)} ${_signed(total)} '
        '(base ${_signed(base)}, situação ${_signed(situational)})';
  }

  String _signed(int value) => '${value >= 0 ? '+' : ''}$value';

  IconData _intentIcon(String intent) {
    switch (intent) {
      case 'combate':
        return Icons.gavel_rounded;
      case 'furtividade':
        return Icons.visibility_off_outlined;
      case 'social':
        return Icons.forum_outlined;
      case 'investigacao':
        return Icons.search;
      case 'movimento':
        return Icons.directions_run;
      case 'resistencia':
        return Icons.shield_outlined;
      default:
        return Icons.auto_awesome;
    }
  }

  Widget _choiceCard(Choice choice, int index) {
    final color = choice.risk ? kAccent : kPrimary;
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 0, 12, 6),
      child: Material(
        color: kSurface.withValues(alpha: .72),
        borderRadius: BorderRadius.circular(16),
        child: InkWell(
          borderRadius: BorderRadius.circular(16),
          onTap: () {
            _playSfx('page_turn.wav', volume: .25);
            _sendText(choice.text);
          },
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: kBorder),
            ),
            child: Row(
              children: [
                Container(
                  width: 22,
                  height: 22,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: color.withValues(alpha: .16),
                    shape: BoxShape.circle,
                  ),
                  child: Text(
                    '${index + 1}',
                    style: TextStyle(
                      color: color,
                      fontSize: 10.5,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        choice.text,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12.5, height: 1.35),
                      ),
                      const SizedBox(height: 5),
                      Wrap(
                        crossAxisAlignment: WrapCrossAlignment.center,
                        spacing: 6,
                        runSpacing: 4,
                        children: [
                          Icon(
                            _intentIcon(choice.intent),
                            size: 12,
                            color: color,
                          ),
                          Text(
                            choice.label,
                            style: TextStyle(
                              color: color,
                              fontSize: 10.5,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          if (choice.risk)
                            Container(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 6,
                                vertical: 2,
                              ),
                              decoration: BoxDecoration(
                                color: kAccent.withValues(alpha: .12),
                                borderRadius: BorderRadius.circular(8),
                                border: Border.all(
                                  color: kAccent.withValues(alpha: .3),
                                ),
                              ),
                              child: Text(
                                choice.difficulty.isEmpty
                                    ? 'Teste · CD ${choice.dc}'
                                    : 'Teste · Dificuldade ${choice.difficulty}',
                                style: const TextStyle(
                                  color: kAccent,
                                  fontSize: 9.5,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ),
                        ],
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 6),
                const Icon(Icons.north_east, size: 14, color: kMuted),
              ],
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.character;
    final baseTitle = _sessionTitle.trim().isEmpty ? 'Narração' : _sessionTitle;
    final subtitle = _chapterCount > 0
        ? '$baseTitle • Capítulo $_chapterCount'
        : baseTitle;
    return LuminusBackground(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          title: Row(
            children: [
              LuminusAvatar(
                radius: 17,
                url: c.avatar,
                letter: c.name.isNotEmpty ? c.name[0] : '?',
                ring: true,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      c.name,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                    Text(
                      subtitle,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: kMuted, fontSize: 11.5),
                    ),
                  ],
                ),
              ),
            ],
          ),
          actions: [
            IconButton(
              tooltip: 'Modo de leitura',
              visualDensity: VisualDensity.compact,
              onPressed: _pickViewMode,
              icon: Icon(
                _viewMode == _ViewMode.scene
                    ? Icons.theater_comedy_outlined
                    : _viewMode == _ViewMode.reader
                    ? Icons.menu_book_outlined
                    : Icons.forum_outlined,
                color: kAccent,
                size: 20,
              ),
            ),
            Padding(
              padding: const EdgeInsets.only(right: 2),
              child: InkWell(
                onTap: _showRelationship,
                borderRadius: BorderRadius.circular(20),
                child: Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 9,
                    vertical: 5,
                  ),
                  decoration: BoxDecoration(
                    color: kSurface2,
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: kBorder),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.favorite, size: 13, color: kAccent),
                      const SizedBox(width: 4),
                      Text(
                        _cast.isEmpty
                            ? 'NR $_nr'
                            : 'NR $_nr · +${_cast.length}',
                        style: const TextStyle(
                          fontSize: 11.5,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            PopupMenuButton<String>(
              icon: const Icon(Icons.more_horiz),
              color: kSurface2,
              onSelected: (v) {
                if (v == 'view') _pickViewMode();
                if (v == 'journal') _showJournal();
                if (v == 'branches') _showBranchTree();
                if (v == 'codex') _showCodex();
                if (v == 'chapters') _showChapters();
                if (v == 'digest') _openDigest();
                if (v == 'director') _showDirector();
                if (v == 'search') _showSearch();
                if (v == 'export') _exportChronicle();
                if (v == 'backlog') _showBacklog();
                if (v == 'sheet') _showSheet();
                if (v == 'memory') _showMemory();
                if (v == 'lore') _showLore();
                if (v == 'voices') _showVoices();
                if (v == 'scene') _generateScene();
                if (v == 'narration') _toggleAutoplay();
                if (v == 'sound') _toggleSound();
                if (v == 'copy') _copyStory();
                if (v == 'reset') _reset();
              },
              itemBuilder: (_) => [
                PopupMenuItem(
                  value: 'view',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      _viewMode == _ViewMode.scene
                          ? Icons.theater_comedy_outlined
                          : _viewMode == _ViewMode.reader
                          ? Icons.menu_book_outlined
                          : Icons.forum_outlined,
                      color: kAccent,
                    ),
                    title: Text('Modo: ${_viewModeLabel(_viewMode)}'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'branches',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.call_split),
                    title: Text('Ramificações'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'codex',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.menu_book_outlined),
                    title: Text('Códice'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'journal',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.account_tree_outlined),
                    title: Text('Diário de fios'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'chapters',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.list_alt),
                    title: Text('Capítulos'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'digest',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.public),
                    title: Text('Crônica do mundo'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'director',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.monitor_heart_outlined),
                    title: Text('Painel do Diretor'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'search',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.search),
                    title: Text('Buscar na história'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'export',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.ios_share),
                    title: Text('Exportar crônica'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'backlog',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.history),
                    title: Text('Backlog'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'sheet',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.badge_outlined),
                    title: Text('Ficha e inventário'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'memory',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.auto_stories_outlined),
                    title: Text('Memória'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'lore',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.public),
                    title: Text('Lore do mundo'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'voices',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.mark_email_unread_outlined),
                    title: Text('Vozes do mundo'),
                  ),
                ),
                PopupMenuItem(
                  value: 'narration',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      _autoplay ? Icons.volume_up : Icons.volume_off,
                    ),
                    title: Text(_autoplay ? 'Narração: ON' : 'Narração: OFF'),
                  ),
                ),
                PopupMenuItem(
                  value: 'sound',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      _soundOn ? Icons.music_note : Icons.music_off,
                    ),
                    title: Text(_soundOn ? 'Som: ON' : 'Som: OFF'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'scene',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.image_outlined, color: kAccent),
                    title: Text('Ilustrar cena'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'copy',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.copy_all_outlined),
                    title: Text('Copiar história'),
                  ),
                ),
                const PopupMenuItem(
                  value: 'reset',
                  child: ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(Icons.restart_alt, color: kDanger),
                    title: Text(
                      'Reiniciar história',
                      style: TextStyle(color: kDanger),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
        body: Stack(
          fit: StackFit.expand,
          children: [
            Column(
              children: [
                Expanded(
                  child: _viewMode == _ViewMode.scene
                      ? _vnStage()
                      : _viewMode == _ViewMode.reader
                      ? _readerStage()
                      : Stack(
                          children: [
                            _backdrop(
                              _sceneImage.trim().isNotEmpty
                                  ? _sceneImage
                                  : c.avatar,
                            ),
                            if (_loading)
                              ListView(
                                padding: const EdgeInsets.fromLTRB(
                                  14,
                                  16,
                                  14,
                                  16,
                                ),
                                children: [
                                  for (var i = 0; i < 4; i++)
                                    Padding(
                                      padding: const EdgeInsets.only(
                                        bottom: 18,
                                      ),
                                      child: Row(
                                        crossAxisAlignment:
                                            CrossAxisAlignment.start,
                                        children: [
                                          const SkeletonBox(
                                            width: 32,
                                            height: 32,
                                            radius: 16,
                                          ),
                                          const SizedBox(width: 9),
                                          Expanded(
                                            child: SkeletonBox(
                                              height: i.isEven ? 92 : 62,
                                              radius: 18,
                                            ),
                                          ),
                                        ],
                                      ),
                                    ),
                                ],
                              )
                            else if (_openingLoading)
                              _openingSplash()
                            else if (_msgs.isEmpty)
                              _emptyChat()
                            else
                              ListView.builder(
                                controller: _scroll,
                                padding: const EdgeInsets.fromLTRB(
                                  12,
                                  12,
                                  12,
                                  18,
                                ),
                                itemCount: _msgs.length + 1,
                                itemBuilder: (_, i) {
                                  if (i == 0) return _sceneHeader();
                                  return _message(_msgs[i - 1]);
                                },
                              ),
                            if (_chaptering)
                              Positioned.fill(
                                child: ColoredBox(
                                  color: Colors.black.withValues(alpha: .65),
                                  child: Center(
                                    child: Container(
                                      margin: const EdgeInsets.all(32),
                                      padding: const EdgeInsets.all(24),
                                      decoration: BoxDecoration(
                                        color: kSurface,
                                        borderRadius: BorderRadius.circular(24),
                                        border: Border.all(color: kBorder),
                                      ),
                                      child: const Column(
                                        mainAxisSize: MainAxisSize.min,
                                        children: [
                                          CircularProgressIndicator(),
                                          SizedBox(height: 16),
                                          Text(
                                            'Resumindo o capítulo...',
                                            style: TextStyle(
                                              fontSize: 16,
                                              fontWeight: FontWeight.w700,
                                            ),
                                          ),
                                          SizedBox(height: 6),
                                          Text(
                                            'Aguarde para continuar.',
                                            style: TextStyle(
                                              color: kMuted,
                                              fontSize: 12,
                                            ),
                                          ),
                                        ],
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                            if (_generatingScene)
                              Positioned.fill(
                                child: ColoredBox(
                                  color: Colors.black.withValues(alpha: .65),
                                  child: Center(
                                    child: Container(
                                      margin: const EdgeInsets.all(32),
                                      padding: const EdgeInsets.all(24),
                                      decoration: BoxDecoration(
                                        color: kSurface,
                                        borderRadius: BorderRadius.circular(24),
                                        border: Border.all(color: kBorder),
                                      ),
                                      child: const Column(
                                        mainAxisSize: MainAxisSize.min,
                                        children: [
                                          CircularProgressIndicator(),
                                          SizedBox(height: 16),
                                          Text(
                                            'Pintando a cena...',
                                            style: TextStyle(
                                              fontSize: 16,
                                              fontWeight: FontWeight.w700,
                                            ),
                                          ),
                                          SizedBox(height: 6),
                                          Text(
                                            'Depois o narrador volta a carregar. Isso pode levar alguns minutos.',
                                            textAlign: TextAlign.center,
                                            style: TextStyle(
                                              color: kMuted,
                                              fontSize: 12,
                                            ),
                                          ),
                                        ],
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                          ],
                        ),
                ),
                SafeArea(
                  top: false,
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (_openingFailed && !_openingLoading)
                        _openingFailedBanner(),
                      _partyStrip(),
                      _choicesBar(),
                      if (!_sending &&
                          !_chaptering &&
                          !_openingLoading &&
                          !_generatingScene)
                        Padding(
                          padding: const EdgeInsets.fromLTRB(14, 4, 14, 2),
                          child: SingleChildScrollView(
                            scrollDirection: Axis.horizontal,
                            child: Row(
                              children: [
                                _quickPill(
                                  Icons.fast_forward_rounded,
                                  'Continuar',
                                  _continueScene,
                                ),
                                const SizedBox(width: 8),
                                _quickPill(
                                  Icons.visibility_outlined,
                                  'Observar',
                                  () => _sendText(
                                    'Eu observo o ambiente com atenção.',
                                  ),
                                ),
                                const SizedBox(width: 8),
                                _quickPill(
                                  Icons.forum_outlined,
                                  'Falar',
                                  () => _focusComposer('Eu digo: '),
                                ),
                                const SizedBox(width: 8),
                                _quickPill(
                                  Icons.touch_app_outlined,
                                  'Agir',
                                  () => _focusComposer('Eu '),
                                ),
                                const SizedBox(width: 8),
                                _quickPill(
                                  Icons.backpack_outlined,
                                  'Usar',
                                  () => _focusComposer('Eu uso '),
                                ),
                                const SizedBox(width: 8),
                                _quickPill(
                                  Icons.map_outlined,
                                  'Viajar',
                                  () => _focusComposer('Eu vou para '),
                                ),
                                const SizedBox(width: 8),
                                _quickPill(
                                  _worldLive ? Icons.public : Icons.public_off,
                                  _worldLive ? 'Mundo vivo ON' : 'Mundo vivo',
                                  _toggleWorldLive,
                                  active: _worldLive,
                                ),
                                const SizedBox(width: 8),
                                _quickPill(
                                  _autoplay
                                      ? Icons.volume_up_rounded
                                      : Icons.volume_off_rounded,
                                  _autoplay ? 'Narração ON' : 'Narração OFF',
                                  _toggleAutoplay,
                                  active: _autoplay,
                                ),
                                const SizedBox(width: 8),
                                _quickPill(
                                  Icons.theater_comedy_outlined,
                                  _ooc ? 'OOC ON' : 'OOC',
                                  () => setState(() => _ooc = !_ooc),
                                  active: _ooc,
                                ),
                              ],
                            ),
                          ),
                        ),
                      if (_ooc)
                        Padding(
                          padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                          child: Row(
                            children: [
                              const Icon(
                                Icons.theater_comedy_outlined,
                                size: 14,
                                color: kAccent,
                              ),
                              const SizedBox(width: 6),
                              const Expanded(
                                child: Text(
                                  'OOC ativo — a próxima mensagem será uma instrução.',
                                  style: TextStyle(
                                    color: kAccent,
                                    fontSize: 11.5,
                                  ),
                                ),
                              ),
                              InkWell(
                                onTap: () => setState(() => _ooc = false),
                                child: const Icon(
                                  Icons.close,
                                  size: 14,
                                  color: kMuted,
                                ),
                              ),
                            ],
                          ),
                        ),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(10, 6, 10, 10),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 4,
                            vertical: 4,
                          ),
                          decoration: BoxDecoration(
                            color: kSurface,
                            borderRadius: BorderRadius.circular(24),
                            border: Border.all(color: kBorder),
                          ),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.end,
                            children: [
                              IconButton(
                                tooltip: 'Mais',
                                onPressed:
                                    (_sending ||
                                        _chaptering ||
                                        _openingLoading ||
                                        _generatingScene)
                                    ? null
                                    : _showComposerMenu,
                                icon: const Icon(Icons.add_circle_outline),
                                color: kMuted,
                              ),
                              Expanded(
                                child: TextField(
                                  controller: _input,
                                  focusNode: _inputFocus,
                                  enabled:
                                      !_chaptering &&
                                      !_openingLoading &&
                                      !_generatingScene,
                                  minLines: 1,
                                  maxLines: 5,
                                  textCapitalization:
                                      TextCapitalization.sentences,
                                  style: const TextStyle(
                                    fontSize: 14.5,
                                    height: 1.4,
                                  ),
                                  decoration: InputDecoration(
                                    hintText: _ooc
                                        ? 'Instrução fora do personagem...'
                                        : 'O que você faz ou diz...',
                                    filled: false,
                                    border: InputBorder.none,
                                    enabledBorder: InputBorder.none,
                                    focusedBorder: InputBorder.none,
                                    contentPadding: const EdgeInsets.symmetric(
                                      vertical: 10,
                                    ),
                                    isDense: true,
                                  ),
                                ),
                              ),
                              const SizedBox(width: 4),
                              Padding(
                                padding: const EdgeInsets.only(bottom: 2),
                                child: IconButton.filled(
                                  onPressed:
                                      (_sending ||
                                          _chaptering ||
                                          _openingLoading ||
                                          _generatingScene)
                                      ? null
                                      : _send,
                                  icon: _sending
                                      ? const SizedBox(
                                          width: 18,
                                          height: 18,
                                          child: CircularProgressIndicator(
                                            strokeWidth: 2,
                                          ),
                                        )
                                      : const Icon(Icons.arrow_upward_rounded),
                                  style: IconButton.styleFrom(
                                    backgroundColor: kPrimary,
                                    foregroundColor: Colors.white,
                                    disabledBackgroundColor: kSurface2,
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            if (_dice != null)
              Positioned.fill(
                child: DiceRollOverlay(receipt: _dice!, onDismiss: _hideDice),
              ),
          ],
        ),
      ),
    );
  }
}

class _SearchSheet extends StatefulWidget {
  final int sessionId;
  final String characterName;
  const _SearchSheet({required this.sessionId, required this.characterName});

  @override
  State<_SearchSheet> createState() => _SearchSheetState();
}

class _SearchSheetState extends State<_SearchSheet> {
  final _controller = TextEditingController();
  List<ChatMessage> _results = const [];
  bool _busy = false;
  String _error = '';
  String _query = '';

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _search([String? value]) async {
    final query = (value ?? _controller.text).trim();
    if (query.length < 2) {
      setState(() {
        _error = 'Digite ao menos 2 caracteres.';
        _results = const [];
        _query = '';
      });
      return;
    }
    setState(() {
      _busy = true;
      _error = '';
      _query = query;
    });
    try {
      final results = await ApiClient.searchMessages(widget.sessionId, query);
      if (!mounted) return;
      setState(() {
        _results = results;
        _busy = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = ApiClient.friendly(e);
        _busy = false;
      });
    }
  }

  void _open(ChatMessage message) {
    final isUser = message.role == 'user';
    showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: kSurface,
        title: Text(isUser ? 'Você' : widget.characterName),
        content: SingleChildScrollView(
          child: isUser
              ? Text(
                  message.content,
                  style: const TextStyle(fontSize: 14, height: 1.5),
                )
              : StoryText(
                  text: message.content,
                  style: storyStyle(size: 15, height: 1.6),
                ),
        ),
        actions: [
          TextButton(
            onPressed: () async {
              await Clipboard.setData(ClipboardData(text: message.content));
              if (dialogContext.mounted) Navigator.pop(dialogContext);
            },
            child: const Text('Copiar'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Fechar'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final keyboard = MediaQuery.viewInsetsOf(context).bottom;
    return AnimatedPadding(
      duration: const Duration(milliseconds: 180),
      padding: EdgeInsets.only(bottom: keyboard),
      child: SizedBox(
        height: MediaQuery.sizeOf(context).height * .82,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(18, 14, 18, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 14),
                  decoration: BoxDecoration(
                    color: kBorder,
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
              ),
              const Text(
                'Buscar na história',
                style: TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
              ),
              const SizedBox(height: 4),
              const Text(
                'Procura em todas as mensagens desta campanha.',
                style: TextStyle(color: kMuted, fontSize: 12),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _controller,
                autofocus: true,
                textInputAction: TextInputAction.search,
                onSubmitted: _search,
                decoration: InputDecoration(
                  hintText: 'nome, lugar, objeto, frase...',
                  prefixIcon: const Icon(Icons.search, size: 20),
                  suffixIcon: IconButton(
                    tooltip: 'Buscar',
                    onPressed: _busy ? null : () => _search(),
                    icon: const Icon(Icons.arrow_forward, size: 20),
                  ),
                ),
              ),
              const SizedBox(height: 10),
              if (_busy) const LinearProgressIndicator(minHeight: 3),
              if (_error.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    _error,
                    style: const TextStyle(color: kDanger, fontSize: 12),
                  ),
                ),
              if (!_busy &&
                  _query.isNotEmpty &&
                  _results.isEmpty &&
                  _error.isEmpty)
                const Padding(
                  padding: EdgeInsets.only(top: 18),
                  child: Center(
                    child: Text(
                      'Nada encontrado.',
                      style: TextStyle(color: kMuted),
                    ),
                  ),
                ),
              const SizedBox(height: 8),
              Expanded(
                child: ListView.separated(
                  itemCount: _results.length,
                  separatorBuilder: (_, _) => const Divider(height: 18),
                  itemBuilder: (_, i) {
                    final message = _results[i];
                    final isUser = message.role == 'user';
                    final text = message.content.replaceAll(
                      RegExp(r'\s+'),
                      ' ',
                    );
                    return InkWell(
                      borderRadius: BorderRadius.circular(10),
                      onTap: () => _open(message),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 4,
                          vertical: 6,
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              isUser
                                  ? 'VOCÊ'
                                  : widget.characterName.toUpperCase(),
                              style: TextStyle(
                                color: isUser ? kPrimary : kAccent,
                                fontSize: 9.5,
                                fontWeight: FontWeight.w700,
                                letterSpacing: 1,
                              ),
                            ),
                            const SizedBox(height: 3),
                            Text(
                              text,
                              maxLines: 3,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 12.5,
                                height: 1.4,
                              ),
                            ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _TypingDots extends StatefulWidget {
  const _TypingDots();

  @override
  State<_TypingDots> createState() => _TypingDotsState();
}

class _TypingDotsState extends State<_TypingDots>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Widget _dot(int index) {
    final wave =
        (math.sin((_controller.value * 2 * math.pi) - index * 0.9) + 1) / 2;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 2),
      child: Transform.translate(
        offset: Offset(0, -2.5 * wave),
        child: Opacity(
          opacity: .3 + .7 * wave,
          child: Container(
            width: 6,
            height: 6,
            decoration: const BoxDecoration(
              color: kMuted,
              shape: BoxShape.circle,
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (_, _) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [for (var i = 0; i < 3; i++) _dot(i)],
      ),
    );
  }
}

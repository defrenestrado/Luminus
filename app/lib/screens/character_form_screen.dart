import 'package:audioplayers/audioplayers.dart';
import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';

class CharacterFormScreen extends StatefulWidget {
  final String kind;
  final Character? existing;
  const CharacterFormScreen({super.key, required this.kind, this.existing});

  @override
  State<CharacterFormScreen> createState() => _CharacterFormScreenState();
}

class _CharacterFormScreenState extends State<CharacterFormScreen> {
  final _seed = TextEditingController();
  final _tone = TextEditingController();
  final _refine = TextEditingController();

  final _name = TextEditingController();
  final _tagline = TextEditingController();
  final _descPublic = TextEditingController();
  final _appearance = TextEditingController();
  final _personality = TextEditingController();
  final _backstory = TextEditingController();
  final _speech = TextEditingController();
  final _likes = TextEditingController();
  final _dislikes = TextEditingController();
  final _fears = TextEditingController();
  final _relationship = TextEditingController();
  final _secrets = TextEditingController();
  final _example = TextEditingController();
  final _greeting = TextEditingController();
  final _avatar = TextEditingController();
  final _tags = TextEditingController();
  final _persona = TextEditingController();

  final _page = PageController();
  final _player = AudioPlayer();

  List<World> _worlds = [];
  List<String> _voices = [];
  int? _worldId;
  String _voice = '';
  bool _busy = false;
  bool _speaking = false;
  bool _generatingCover = false;
  int _step = 0;
  String _status = '';
  String _coverStyle = 'anime';
  String _visualPrompt = '';
  int _artSeed = 0;
  String _visualStyle = 'anime';
  final Map<String, int> _attributes = {
    'corpo': 10,
    'mente': 10,
    'presenca': 10,
    'sorte': 10,
  };
  final Map<String, int> _skills = {
    'luta': 0,
    'atletismo': 0,
    'vigor': 0,
    'furtividade': 0,
    'investigacao': 0,
    'persuasao': 0,
  };

  static const _attributeLabels = {
    'corpo': 'Corpo',
    'mente': 'Mente',
    'presenca': 'Presença',
    'sorte': 'Sorte',
  };

  static const _skillLabels = {
    'luta': 'Luta',
    'atletismo': 'Atletismo',
    'vigor': 'Vigor',
    'furtividade': 'Furtividade',
    'investigacao': 'Investigação',
    'persuasao': 'Persuasão',
  };

  static const _skillPool = 10;

  int get _skillTotal => _skills.values.fold(0, (sum, value) => sum + value);

  bool get isNpc => widget.kind == 'npc';
  bool get _isLast => _step == 2;

  static const _stepLabels = ['Identidade', 'Alma', 'Revisão'];

  @override
  void initState() {
    super.initState();
    final e = widget.existing;
    if (e != null) {
      _name.text = e.name;
      _tagline.text = e.tagline;
      _descPublic.text = e.descriptionPublic;
      _appearance.text = e.appearance;
      _personality.text = e.personality;
      _backstory.text = e.backstory;
      _speech.text = e.speechStyle;
      _likes.text = e.likes;
      _dislikes.text = e.dislikes;
      _fears.text = e.fears;
      _relationship.text = e.relationship;
      _secrets.text = e.secrets;
      _example.text = e.exampleDialogue;
      _greeting.text = e.greeting;
      _avatar.text = e.avatar;
      _tags.text = e.tags.join(', ');
      _persona.text = e.persona;
      _worldId = e.worldId;
      _voice = e.voice;
      _visualPrompt = e.visualPrompt;
      _artSeed = e.artSeed;
      _visualStyle = e.visualStyle;
      if (e.attributes.isNotEmpty) {
        _attributes.addAll(e.attributes);
      }
      if (e.skills.isNotEmpty) {
        _skills.addAll(e.skills);
      }
    }
    _name.addListener(_clearVisualIdentity);
    _appearance.addListener(_clearVisualIdentity);
    _tags.addListener(_clearVisualIdentity);
    _loadWorlds();
    _loadVoices();
  }

  void _clearVisualIdentity() {
    _visualPrompt = '';
    _artSeed = 0;
    if (_avatar.text.trim().startsWith('/art/')) {
      _avatar.text = '';
    }
  }

  @override
  void dispose() {
    for (final controller in _controllers) {
      controller.dispose();
    }
    _page.dispose();
    _player.dispose();
    super.dispose();
  }

  List<TextEditingController> get _controllers => [
    _seed,
    _tone,
    _refine,
    _tags,
    ..._map.values,
  ];

  Future<void> _loadVoices() async {
    try {
      final v = await ApiClient.voices();
      if (mounted) setState(() => _voices = v);
    } catch (_) {}
  }

  Future<void> _loadWorlds() async {
    try {
      final w = await ApiClient.worlds();
      if (!mounted) return;
      setState(() {
        _worlds = w;
        if (widget.existing == null && _worldId == null && w.isNotEmpty) {
          final canonical = w
              .where((x) => x.name.toLowerCase() == 'nadir')
              .toList();
          _worldId = canonical.isNotEmpty ? canonical.first.id : w.first.id;
        }
      });
    } catch (_) {}
  }

  Map<String, TextEditingController> get _map => {
    'name': _name,
    'tagline': _tagline,
    'description_public': _descPublic,
    'appearance': _appearance,
    'personality': _personality,
    'backstory': _backstory,
    'speech_style': _speech,
    'likes': _likes,
    'dislikes': _dislikes,
    'fears': _fears,
    'relationship': _relationship,
    'secrets': _secrets,
    'example_dialogue': _example,
    'greeting': _greeting,
    'avatar': _avatar,
    'persona': _persona,
  };

  void _fillFromDraft(Map<String, dynamic> d) {
    for (final e in _map.entries) {
      final v = d[e.key];
      if (v is String && v.isNotEmpty) e.value.text = v;
    }
    final t = d['tags'];
    if (t is List) _tags.text = t.map((e) => e.toString()).join(', ');
    final attrs = d['attributes'];
    if (attrs is Map) {
      for (final key in _attributeLabels.keys) {
        final value = attrs[key];
        final parsed = value is num
            ? value.toInt()
            : int.tryParse('${value ?? ''}');
        if (parsed != null) {
          _attributes[key] = parsed.clamp(3, 18);
        }
      }
    }
    final skills = d['skills'];
    if (skills is Map) {
      for (final key in _skillLabels.keys) {
        final value = skills[key];
        final parsed = value is num
            ? value.toInt()
            : int.tryParse('${value ?? ''}');
        if (parsed != null) {
          _skills[key] = parsed.clamp(0, 5);
        }
      }
    }
  }

  Map<String, dynamic> _draftFromFields() => {
    for (final e in _map.entries) e.key: e.value.text.trim(),
    'tags': _tags.text
        .split(',')
        .map((e) => e.trim())
        .where((e) => e.isNotEmpty)
        .toList(),
    'attributes': Map<String, int>.from(_attributes),
    'skills': Map<String, int>.from(_skills),
  };

  String _errorText(Object e) {
    final text = e.toString();
    if (text.startsWith('IA ')) return text;
    return ApiClient.friendly(e);
  }

  void _go(int step) {
    if (step < 0 || step > 2) return;
    setState(() {
      _step = step;
      _status = '';
    });
    if (_page.hasClients) {
      _page.animateToPage(
        step,
        duration: const Duration(milliseconds: 260),
        curve: Curves.easeOutCubic,
      );
    }
  }

  void _next() {
    if (_step == 0 && _name.text.trim().isEmpty) {
      setState(() => _status = 'Dê um nome ao personagem antes de continuar.');
      return;
    }
    _go(_step + 1);
  }

  Future<void> _completar() async {
    if (_seed.text.trim().isEmpty) {
      setState(() => _status = 'Escreva uma ideia primeiro.');
      return;
    }
    setState(() {
      _busy = true;
      _status = 'A IA está completando sua ideia... (pode levar ~1-2 min)';
    });
    try {
      final d = await ApiClient.assistCharacter(
        seed: _seed.text.trim(),
        kind: widget.kind,
        tone: _tone.text.trim(),
        name: _name.text.trim(),
        worldId: _worldId,
      );
      _fillFromDraft(d);
      setState(() => _status = 'Pronto. Revise e edite o que quiser.');
    } catch (e) {
      setState(() => _status = _errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _refinar() async {
    if (_refine.text.trim().isEmpty) {
      setState(
        () => _status = 'Escreva o que quer mudar (ex.: "mais sombrio").',
      );
      return;
    }
    setState(() {
      _busy = true;
      _status = 'Refinando com IA...';
    });
    try {
      final d = await ApiClient.assistRefine(
        draft: _draftFromFields(),
        instruction: _refine.text.trim(),
        kind: widget.kind,
      );
      _fillFromDraft(d);
      _refine.clear();
      setState(() => _status = 'Refinado.');
    } catch (e) {
      setState(() => _status = _errorText(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  String _worldName() {
    for (final w in _worlds) {
      if (w.id == _worldId) return w.name;
    }
    return '';
  }

  Future<void> _previewVoice() async {
    final name = _name.text.trim();
    final sample = _greeting.text.trim().isNotEmpty
        ? _greeting.text.trim()
        : 'Olá. Eu sou ${name.isEmpty ? 'este personagem' : name}.';
    final clean = sample
        .replaceAll(RegExp(r'[*#_]+'), '')
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
    setState(() => _speaking = true);
    try {
      final bytes = await ApiClient.tts(clean, voice: _voice);
      await _player.stop();
      await _player.play(BytesSource(bytes));
    } catch (e) {
      _snack(ApiClient.friendly(e));
    } finally {
      if (mounted) setState(() => _speaking = false);
    }
  }

  Future<void> _generateCover() async {
    if (_generatingCover) return;
    if (_name.text.trim().isEmpty) {
      _snack('Dê um nome ao personagem antes de gerar a capa.');
      return;
    }
    setState(() => _generatingCover = true);
    try {
      final tags = _tags.text
          .split(',')
          .map((e) => e.trim())
          .where((e) => e.isNotEmpty)
          .toList();
      final result = await ApiClient.generateCover(
        characterId: widget.existing?.id,
        name: _name.text.trim(),
        kind: widget.kind,
        appearance: _appearance.text.trim(),
        personality: _personality.text.trim(),
        backstory: _backstory.text.trim(),
        tags: tags,
        world: _worldName(),
        style: _coverStyle,
      );
      final url = (result['url'] ?? '').toString();
      if (url.isEmpty) throw 'A API não retornou a imagem.';
      final warning = (result['warning'] ?? '').toString();
      final source = (result['prompt_source'] ?? '').toString();
      final visualPrompt = (result['visual_prompt'] ?? '').toString();
      final artSeed = (result['art_seed'] as num?)?.toInt() ?? 0;
      final visualStyle = (result['visual_style'] ?? '').toString().trim();
      setState(() {
        _avatar.text = url;
        if (visualPrompt.isNotEmpty) {
          _visualPrompt = visualPrompt;
          _artSeed = artSeed;
          _visualStyle = visualStyle.isEmpty ? _coverStyle : visualStyle;
        }
      });
      _snack(
        warning.isNotEmpty
            ? warning
            : source == 'deepseek'
            ? 'Capa gerada com direção de arte da DeepSeek.'
            : 'Capa gerada.',
      );
    } catch (e) {
      _snack(e is String ? e : _errorText(e));
    } finally {
      if (mounted) setState(() => _generatingCover = false);
    }
  }

  Future<void> _save() async {
    if (_name.text.trim().isEmpty) {
      _go(0);
      setState(() => _status = 'O nome é obrigatório.');
      return;
    }
    setState(() => _busy = true);
    try {
      final body = <String, dynamic>{
        'kind': widget.kind,
        'name': _name.text.trim(),
        'avatar': _avatar.text.trim(),
        'tagline': _tagline.text.trim(),
        'description_public': _descPublic.text.trim(),
        'persona': _persona.text.trim(),
        'greeting': _greeting.text.trim(),
        'appearance': _appearance.text.trim(),
        'personality': _personality.text.trim(),
        'backstory': _backstory.text.trim(),
        'speech_style': _speech.text.trim(),
        'likes': _likes.text.trim(),
        'dislikes': _dislikes.text.trim(),
        'fears': _fears.text.trim(),
        'relationship': _relationship.text.trim(),
        'secrets': _secrets.text.trim(),
        'example_dialogue': _example.text.trim(),
        'tags': _tags.text
            .split(',')
            .map((e) => e.trim())
            .where((e) => e.isNotEmpty)
            .toList(),
        'world_id': _worldId,
        'voice': _voice,
        'visual_prompt': _visualPrompt,
        'art_seed': _artSeed,
        'visual_style': _visualStyle,
        'attributes': Map<String, int>.from(_attributes),
        'skills': Map<String, int>.from(_skills),
      };
      if (widget.existing != null) {
        await ApiClient.updateCharacter(widget.existing!.id, body);
      } else {
        await ApiClient.createCharacter(body);
      }
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      setState(() {
        _busy = false;
        _status = _errorText(e);
      });
    }
  }

  Widget _field(
    TextEditingController c,
    String label, {
    int lines = 1,
    String? hint,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: c,
        minLines: lines,
        maxLines: lines + 3,
        decoration: InputDecoration(
          labelText: label,
          hintText: hint,
          border: const OutlineInputBorder(),
          alignLabelWithHint: lines > 1,
        ),
      ),
    );
  }

  Widget _sectionTitle(String text) {
    return Padding(
      padding: const EdgeInsets.only(top: 10, bottom: 9),
      child: Text(
        text.toUpperCase(),
        style: const TextStyle(
          color: kMuted,
          fontSize: 10.5,
          fontWeight: FontWeight.w700,
          letterSpacing: 1.4,
        ),
      ),
    );
  }

  Widget _statusBanner() {
    if (_status.isEmpty) return const SizedBox.shrink();
    return Container(
      margin: const EdgeInsets.only(top: 4),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: kSurface2,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: kBorder),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.info_outline, size: 16, color: kAccent),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              _status,
              style: const TextStyle(
                color: kMuted,
                fontSize: 12.5,
                height: 1.4,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _aiCard() {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(Icons.auto_awesome, size: 18, color: kAccent),
                const SizedBox(width: 7),
                Text(
                  'Crie com a IA',
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ],
            ),
            const SizedBox(height: 4),
            const Text(
              'Descreva a ideia em uma frase. A IA preenche a ficha inteira.',
              style: TextStyle(color: kMuted, fontSize: 12, height: 1.4),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: _seed,
              minLines: 2,
              maxLines: 5,
              decoration: const InputDecoration(
                labelText: 'Sua ideia',
                hintText: 'ex.: uma espadachim tsundere, filha de um general traído...',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 8),
            TextField(
              controller: _tone,
              decoration: const InputDecoration(
                labelText: 'Tom/estilo (opcional)',
                hintText: 'grimdark, anime, romance, comédia...',
                border: OutlineInputBorder(),
              ),
            ),
            const SizedBox(height: 10),
            FilledButton.icon(
              onPressed: _busy ? null : _completar,
              icon: const Icon(Icons.auto_awesome),
              label: const Text('Completar com IA'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _identityStep() {
    final letter = _name.text.isNotEmpty ? _name.text[0] : '?';
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      children: [
        _aiCard(),
        _statusBanner(),
        _sectionTitle('Identidade'),
        _field(_name, 'Nome', hint: 'Ex.: Seraphine Valdris'),
        _field(
          _tagline,
          'Tagline',
          hint: 'Uma frase curta que resume o personagem',
        ),
        _field(_descPublic, 'Descrição pública', lines: 3),
        _field(_appearance, 'Aparência', lines: 3),
        _sectionTitle('Capa e voz'),
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.center,
            children: [
              LuminusAvatar(radius: 30, url: _avatar.text, letter: letter),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: _avatar,
                  onChanged: (_) => setState(() {}),
                  decoration: const InputDecoration(
                    labelText: 'Avatar (URL ou capa gerada)',
                    hintText: 'https://... ou /art/...',
                    hintStyle: TextStyle(fontSize: 12),
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.image_outlined, size: 20),
                  ),
                ),
              ),
            ],
          ),
        ),
        _sectionTitle('Capa com IA'),
        Wrap(
          spacing: 6,
          runSpacing: 6,
          children: [
            for (final style in const [
              ('anime', 'Anime'),
              ('pintura', 'Pintura'),
              ('grimdark', 'Grimdark'),
              ('aquarela', 'Aquarela'),
            ])
              ChoiceChip(
                label: Text(style.$2),
                selected: _coverStyle == style.$1,
                onSelected: (_) => setState(() => _coverStyle = style.$1),
              ),
          ],
        ),
        const SizedBox(height: 10),
        SizedBox(
          width: double.infinity,
          child: OutlinedButton.icon(
            onPressed: (_generatingCover || _busy) ? null : _generateCover,
            icon: _generatingCover
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.auto_awesome),
            label: Text(
              _generatingCover
                  ? 'Gerando capa...'
                  : (_avatar.text.isEmpty
                        ? 'Gerar capa com IA'
                        : 'Gerar outra capa'),
            ),
          ),
        ),
        const SizedBox(height: 6),
        const Text(
          'A ficha resumida é enviada à DeepSeek; a imagem é renderizada no seu servidor.',
          style: TextStyle(color: kMuted, fontSize: 10.5, height: 1.4),
        ),
        const SizedBox(height: 12),
        _field(
          _tags,
          'Tags',
          hint: 'nobre, vingativa, magia (separadas por vírgula)',
        ),
        const SizedBox(height: 2),
        DropdownButtonFormField<int>(
          key: ValueKey('world-$_worldId'),
          initialValue: _worldId,
          decoration: const InputDecoration(
            labelText: 'Mundo',
            border: OutlineInputBorder(),
          ),
          items: _worlds
              .map((w) => DropdownMenuItem(value: w.id, child: Text(w.name)))
              .toList(),
          onChanged: (v) => setState(() => _worldId = v),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: DropdownButtonFormField<String>(
                initialValue: _voice.isEmpty ? null : _voice,
                decoration: const InputDecoration(
                  labelText: 'Voz',
                  border: OutlineInputBorder(),
                ),
                items: _voices
                    .map((v) => DropdownMenuItem(value: v, child: Text(v)))
                    .toList(),
                onChanged: (v) => setState(() => _voice = v ?? ''),
              ),
            ),
            const SizedBox(width: 10),
            OutlinedButton(
              onPressed: _speaking ? null : _previewVoice,
              child: _speaking
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.volume_up_outlined),
            ),
          ],
        ),
      ],
    );
  }

  Widget _attributeSlider(String key, String label) {
    final value = _attributes[key] ?? 10;
    final divisor = key == 'sorte' ? 4 : 2;
    final mod = ((value - 10) / divisor).floor();
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Row(
        children: [
          SizedBox(
            width: 74,
            child: Text(
              label,
              style: const TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          Expanded(
            child: Slider(
              min: 3,
              max: 18,
              divisions: 15,
              value: value.toDouble(),
              label: '$value',
              onChanged: (v) => setState(() => _attributes[key] = v.round()),
            ),
          ),
          SizedBox(
            width: 64,
            child: Text(
              '$value (${mod >= 0 ? '+' : ''}$mod)',
              textAlign: TextAlign.right,
              style: const TextStyle(color: kMuted, fontSize: 11.5),
            ),
          ),
        ],
      ),
    );
  }

  Widget _skillStepper(String key, String label) {
    final value = _skills[key] ?? 0;
    final canRaise = _skillTotal < _skillPool;
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              style: const TextStyle(
                fontSize: 12.5,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          IconButton(
            visualDensity: VisualDensity.compact,
            tooltip: 'Diminuir $label',
            onPressed: value <= 0
                ? null
                : () => setState(() => _skills[key] = value - 1),
            icon: const Icon(Icons.remove_circle_outline, size: 20),
          ),
          SizedBox(
            width: 20,
            child: Text(
              '$value',
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
            ),
          ),
          IconButton(
            visualDensity: VisualDensity.compact,
            tooltip: 'Aumentar $label',
            onPressed: !canRaise || value >= 5
                ? null
                : () => setState(() => _skills[key] = value + 1),
            icon: const Icon(Icons.add_circle_outline, size: 20),
          ),
        ],
      ),
    );
  }

  Widget _soulStep() {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      children: [
        _statusBanner(),
        if (!isNpc) ...[
          _sectionTitle('Atributos'),
          const Text(
            'Escala 3–18; 10 é humano médio. Corpo cobre combate/movimento/resistência, '
            'Mente cobre furtividade/investigação, Presença cobre social e Sorte soma '
            'em todos os testes.',
            style: TextStyle(color: kMuted, fontSize: 11, height: 1.45),
          ),
          const SizedBox(height: 8),
          for (final entry in _attributeLabels.entries)
            _attributeSlider(entry.key, entry.value),
          const SizedBox(height: 10),
          _sectionTitle('Perícias · $_skillTotal/$_skillPool'),
          const Text(
            'Cada ponto soma no teste correspondente (0–5). Luta/Atletismo/Vigor '
            'usam Corpo; Furtividade/Investigação usam Mente; Persuasão usa Presença.',
            style: TextStyle(color: kMuted, fontSize: 11, height: 1.45),
          ),
          const SizedBox(height: 4),
          for (final entry in _skillLabels.entries)
            _skillStepper(entry.key, entry.value),
          const SizedBox(height: 4),
        ],
        _sectionTitle('Alma'),
        _field(_personality, 'Personalidade e moral', lines: 3),
        _field(_backstory, 'História / objetivos', lines: 3),
        _field(_speech, 'Jeito de falar', lines: 2),
        _sectionTitle('Gostos e medos'),
        _field(_likes, 'Gosta de'),
        _field(_dislikes, 'Não gosta de'),
        _field(_fears, 'Medos'),
        if (isNpc) ...[
          _field(_relationship, 'Relação com o jogador', lines: 2),
          _field(_secrets, 'Segredos / conhecimento', lines: 2),
        ],
      ],
    );
  }

  Widget _reviewStep() {
    String? world;
    for (final w in _worlds) {
      if (w.id == _worldId) {
        world = w.name;
        break;
      }
    }
    final tagList = _tags.text
        .split(',')
        .map((e) => e.trim())
        .where((e) => e.isNotEmpty)
        .take(6)
        .toList();
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      children: [
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: kSurface,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: kBorder),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  LuminusAvatar(
                    radius: 27,
                    url: _avatar.text,
                    letter: _name.text.isNotEmpty ? _name.text[0] : '?',
                    ring: true,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          _name.text.trim().isEmpty
                              ? 'Sem nome'
                              : _name.text.trim(),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 16.5,
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                        if (_tagline.text.trim().isNotEmpty) ...[
                          const SizedBox(height: 2),
                          Text(
                            _tagline.text.trim(),
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              color: kMuted,
                              fontSize: 11.5,
                              height: 1.35,
                            ),
                          ),
                        ],
                      ],
                    ),
                  ),
                ],
              ),
              if (tagList.isNotEmpty) ...[
                const SizedBox(height: 12),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    for (final tag in tagList)
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 8,
                          vertical: 4,
                        ),
                        decoration: BoxDecoration(
                          color: kSurface2,
                          borderRadius: BorderRadius.circular(9),
                          border: Border.all(color: kBorder),
                        ),
                        child: Text(
                          tag,
                          style: const TextStyle(color: kMuted, fontSize: 10.5),
                        ),
                      ),
                  ],
                ),
              ],
              const Divider(height: 24),
              _reviewRow('Mundo', world ?? 'não definido'),
              _reviewRow('Voz', _voice.isEmpty ? 'padrão do servidor' : _voice),
              _reviewRow('Tipo', isNpc ? 'NPC' : 'Protagonista'),
              if (!isNpc) ...[
                _reviewRow(
                  'Atributos',
                  [
                    for (final entry in _attributeLabels.entries)
                      '${entry.value} ${_attributes[entry.key] ?? 10}',
                  ].join(' · '),
                ),
                _reviewRow(
                  'Perícias',
                  _skillTotal == 0
                      ? 'sem treino'
                      : [
                          for (final entry in _skillLabels.entries)
                            if ((_skills[entry.key] ?? 0) > 0)
                              '${entry.value} ${_skills[entry.key]}',
                        ].join(' · '),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 18),
        _sectionTitle('Cena inicial'),
        _field(_greeting, 'Fala / cena inicial', lines: 3),
        _field(_example, 'Diálogo de exemplo', lines: 3),
        _field(_persona, 'Instruções extras (opcional)', lines: 2),
        const SizedBox(height: 4),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    const Icon(Icons.tune, size: 18, color: kPrimary),
                    const SizedBox(width: 7),
                    Text(
                      'Refinar com IA',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: _refine,
                  decoration: const InputDecoration(
                    labelText: 'O que mudar?',
                    hintText: 'ex.: mais sombrio, menos clichê, mais jovem...',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                OutlinedButton.icon(
                  onPressed: _busy ? null : _refinar,
                  icon: const Icon(Icons.auto_fix_high),
                  label: const Text('Refinar ficha'),
                ),
              ],
            ),
          ),
        ),
        _statusBanner(),
      ],
    );
  }

  Widget _reviewRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 7),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 60,
            child: Text(
              label,
              style: const TextStyle(color: kMuted, fontSize: 11.5),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(fontSize: 12.5, height: 1.35),
            ),
          ),
        ],
      ),
    );
  }

  Widget _stepBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
      child: Row(
        children: [
          for (var i = 0; i < 3; i++) ...[
            Expanded(
              child: Column(
                children: [
                  AnimatedContainer(
                    duration: const Duration(milliseconds: 220),
                    height: 3,
                    decoration: BoxDecoration(
                      color: i <= _step ? kPrimary : kBorder,
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                  const SizedBox(height: 5),
                  Text(
                    _stepLabels[i],
                    style: TextStyle(
                      color: i == _step ? kText : kMuted,
                      fontSize: 10.5,
                      fontWeight: i == _step
                          ? FontWeight.w700
                          : FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ),
            if (i < 2) const SizedBox(width: 6),
          ],
        ],
      ),
    );
  }

  Widget _bottomBar() {
    return SafeArea(
      top: false,
      child: Container(
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
        decoration: BoxDecoration(
          color: kBg.withValues(alpha: .94),
          border: const Border(top: BorderSide(color: kBorder)),
        ),
        child: Row(
          children: [
            if (_step > 0) ...[
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _busy ? null : () => _go(_step - 1),
                  icon: const Icon(Icons.arrow_back, size: 18),
                  label: const Text('Voltar'),
                ),
              ),
              const SizedBox(width: 10),
            ],
            Expanded(
              flex: _step > 0 ? 2 : 1,
              child: FilledButton.icon(
                onPressed: _busy ? null : (_isLast ? _save : _next),
                icon: Icon(
                  _isLast ? Icons.save_outlined : Icons.arrow_forward,
                  size: 18,
                ),
                label: Text(
                  _busy
                      ? (_isLast ? 'Salvando...' : 'Aguarde...')
                      : (_isLast ? 'Salvar personagem' : 'Continuar'),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final titulo = widget.existing != null
        ? (isNpc ? 'Editar NPC' : 'Editar personagem principal')
        : (isNpc ? 'Novo NPC' : 'Novo personagem principal');
    return LuminusBackground(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(
          title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(titulo, style: const TextStyle(fontSize: 18)),
              Text(
                'Etapa ${_step + 1} de 3 · ${_stepLabels[_step]}',
                style: const TextStyle(
                  color: kMuted,
                  fontSize: 11.5,
                  fontWeight: FontWeight.w400,
                ),
              ),
            ],
          ),
        ),
        bottomNavigationBar: _bottomBar(),
        body: Column(
          children: [
            _stepBar(),
            Expanded(
              child: PageView(
                controller: _page,
                physics: const NeverScrollableScrollPhysics(),
                onPageChanged: (i) => setState(() => _step = i),
                children: [_identityStep(), _soulStep(), _reviewStep()],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

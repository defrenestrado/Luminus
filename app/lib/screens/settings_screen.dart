import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api.dart';
import '../local_files.dart';
import '../theme.dart';
import '../updater.dart';
import '../widgets/game_icon.dart';
import 'places_screen.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _url;
  late final TextEditingController _adminKey;
  String _status = '';
  String _adminStatus = '';
  String _updateInfo = 'Verificando servidor de APK...';
  String _updateStatus = '';
  bool _downloading = false;
  double _progress = 0;
  List<World> _worlds = [];
  int? _worldId;
  bool _loadingWorlds = false;
  bool _packBusy = false;
  bool _includeSessions = true;
  String _packStatus = '';
  ManaInfo? _mana;
  bool _claimingMana = false;
  double _textScale = 1.0;

  @override
  void initState() {
    super.initState();
    _url = TextEditingController(text: ApiClient.baseUrl);
    _adminKey = TextEditingController(text: ApiClient.adminKey);
    _loadUpdateInfo();
    _loadWorlds();
    _loadMana();
    _loadTextScale();
  }

  Future<void> _loadTextScale() async {
    final p = await SharedPreferences.getInstance();
    if (mounted) {
      setState(() => _textScale = p.getDouble('text_scale') ?? 1.0);
    }
  }

  Future<void> _setTextScale(double value) async {
    setState(() => _textScale = value);
    final p = await SharedPreferences.getInstance();
    await p.setDouble('text_scale', value);
  }

  Future<void> _loadMana() async {
    try {
      final mana = await ApiClient.mana();
      if (mounted) setState(() => _mana = mana);
    } catch (_) {}
  }

  Future<void> _claimMana() async {
    if (_claimingMana) return;
    setState(() => _claimingMana = true);
    try {
      final mana = await ApiClient.claimDailyMana();
      if (!mounted) return;
      setState(() => _mana = mana);
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Mana diária resgatada.')));
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(e is String ? e : ApiClient.friendly(e))),
        );
      }
    } finally {
      if (mounted) setState(() => _claimingMana = false);
    }
  }

  Widget _manaCard() {
    final mana = _mana;
    final balance = mana?.balance ?? 0;
    final available = mana?.dailyAvailable ?? false;
    final daily = mana?.dailyAmount ?? 40;
    final hours = mana == null ? 0 : (mana.nextDailyIn / 3600).ceil();
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(22),
        gradient: const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xFF3A2A78), Color(0xFF1A1536)],
        ),
        border: Border.all(color: kBorder),
      ),
      child: Row(
        children: [
          Container(
            width: 46,
            height: 46,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: kAccent.withValues(alpha: .16),
              border: Border.all(color: kAccent.withValues(alpha: .45)),
            ),
            child: const GameIcon('magic-potion', size: 24, color: kAccent),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '$balance',
                  style: const TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w800,
                    color: kAccent,
                    height: 1.1,
                  ),
                ),
                Text(
                  available
                      ? 'Mana disponível · resgate diário pronto'
                      : hours > 0
                      ? 'Mana · próximo resgate em ${hours}h'
                      : 'Mana',
                  style: const TextStyle(color: kMuted, fontSize: 11.5),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          available
              ? FilledButton(
                  onPressed: _claimingMana ? null : _claimMana,
                  style: FilledButton.styleFrom(
                    backgroundColor: kAccent,
                    foregroundColor: kBg,
                    padding: const EdgeInsets.symmetric(
                      horizontal: 14,
                      vertical: 10,
                    ),
                  ),
                  child: _claimingMana
                      ? const SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : Text('+$daily'),
                )
              : const Icon(Icons.hourglass_bottom, color: kMuted, size: 20),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _url.dispose();
    _adminKey.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    var url = _url.text.trim();
    if (url.isEmpty) return;
    if (!url.startsWith('http')) url = 'http://$url';
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server_url', url);
    ApiClient.baseUrl = url;
    if (!mounted) return;
    setState(() => _status = 'Servidor salvo.');
  }

  Future<void> _test() async {
    setState(() => _status = 'Testando...');
    try {
      final h = await ApiClient.health();
      final models = (h['models'] as List?)?.length ?? 0;
      final art = (h['art'] as Map?) ?? const {};
      final artInfo = art['deepseek'] == true
          ? 'DeepSeek ON'
          : 'DeepSeek OFF (prompt local)';
      setState(
        () => _status = h['ollama'] == true
            ? 'Conectado — Ollama online, $models modelos. Arte: $artInfo. Padrão: ${h['default_model']}'
            : 'Servidor respondeu, mas Ollama offline. Arte: $artInfo.',
      );
    } catch (e) {
      setState(() => _status = 'Falhou: $e');
    }
  }

  Future<void> _discover() async {
    setState(() => _status = 'Procurando servidor...');
    final url = await ApiClient.discover();
    _url.text = url;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('server_url', url);
    if (!mounted) return;
    setState(() => _status = 'Usando: $url');
  }

  Future<void> _saveAdminKey() async {
    final key = _adminKey.text.trim();
    final prefs = await SharedPreferences.getInstance();
    if (key.isEmpty) {
      await prefs.remove('admin_key');
      ApiClient.adminKey = '';
      if (mounted) setState(() => _adminStatus = 'Chave removida.');
      return;
    }
    await prefs.setString('admin_key', key);
    ApiClient.adminKey = key;
    if (mounted) setState(() => _adminStatus = 'Chave salva.');
  }

  Future<void> _loadUpdateInfo() async {
    final data = await AppUpdater.check();
    if (!mounted) return;
    if (data == null) {
      setState(
        () => _updateInfo = 'Servidor de APK indisponível (porta 8000).',
      );
      return;
    }
    final at = DateTime.tryParse((data['built_at'] ?? '').toString());
    setState(() {
      _updateInfo = at == null
          ? 'APK disponível no servidor.'
          : 'APK disponível: ${at.toLocal()}';
    });
  }

  Future<void> _loadWorlds({bool preferFirst = false}) async {
    if (mounted) setState(() => _loadingWorlds = true);
    try {
      final list = await ApiClient.worlds();
      if (!mounted) return;
      setState(() {
        _worlds = list;
        final current = _worldId;
        final keep = current != null && list.any((w) => w.id == current);
        if (preferFirst || !keep) {
          _worldId = list.isEmpty ? null : list.first.id;
        }
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _worlds = [];
          _worldId = null;
          _packStatus = e is String ? e : ApiClient.friendly(e);
        });
      }
    } finally {
      if (mounted) setState(() => _loadingWorlds = false);
    }
  }

  String _packFileName(World? world, int worldId) {
    final raw = (world?.name ?? 'mundo').toLowerCase();
    final slug = raw
        .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
        .replaceAll(RegExp(r'^-+|-+$'), '');
    final now = DateTime.now();
    final stamp =
        '${now.year}'
        '${now.month.toString().padLeft(2, '0')}'
        '${now.day.toString().padLeft(2, '0')}-'
        '${now.hour.toString().padLeft(2, '0')}'
        '${now.minute.toString().padLeft(2, '0')}';
    return 'luminus-${slug.isEmpty ? 'mundo-$worldId' : slug}-$stamp.json';
  }

  Future<void> _exportPack() async {
    if (_packBusy) return;
    final wid = _worldId;
    if (wid == null) {
      setState(() => _packStatus = 'Escolha um mundo para exportar.');
      return;
    }
    World? world;
    for (final w in _worlds) {
      if (w.id == wid) {
        world = w;
        break;
      }
    }
    setState(() {
      _packBusy = true;
      _packStatus = 'Preparando o World Pack...';
    });
    try {
      final json = await ApiClient.exportWorldPack(
        wid,
        includeSessions: _includeSessions,
      );
      if (!mounted) return;
      final uri = await LocalFiles.exportJson(
        json,
        suggestedName: _packFileName(world, wid),
      );
      if (!mounted) return;
      setState(() {
        _packStatus = uri == null
            ? 'Exportação cancelada.'
            : 'World Pack salvo (${(json.length / 1024).toStringAsFixed(1)} KB).';
      });
    } catch (e) {
      if (mounted) {
        setState(() => _packStatus = e is String ? e : ApiClient.friendly(e));
      }
    } finally {
      if (mounted) setState(() => _packBusy = false);
    }
  }

  Future<void> _importPack() async {
    if (_packBusy) return;
    setState(() {
      _packBusy = true;
      _packStatus = 'Escolha o arquivo do World Pack...';
    });
    try {
      final text = await LocalFiles.pickJson();
      if (!mounted) return;
      if (text == null) {
        setState(() => _packStatus = 'Importação cancelada.');
        return;
      }
      setState(() => _packStatus = 'Importando mundo...');
      final res = await ApiClient.importWorldPack(text);
      if (!mounted) return;
      final counts =
          (res['imported'] as Map?)?.cast<String, dynamic>() ??
          const <String, dynamic>{};
      setState(() {
        _packStatus =
            'Mundo importado: '
            '${counts['characters'] ?? 0} personagens, '
            '${counts['sessions'] ?? 0} histórias, '
            '${counts['messages'] ?? 0} mensagens.';
      });
      await _loadWorlds(preferFirst: true);
    } catch (e) {
      if (mounted) {
        setState(() => _packStatus = e is String ? e : ApiClient.friendly(e));
      }
    } finally {
      if (mounted) setState(() => _packBusy = false);
    }
  }

  Future<void> _downloadAndInstall() async {
    if (_downloading) return;
    setState(() {
      _downloading = true;
      _progress = 0;
      _updateStatus = 'Baixando APK...';
    });
    try {
      await AppUpdater.downloadAndInstall(
        onProgress: (p) {
          if (mounted) setState(() => _progress = p);
        },
        onStatus: (s) {
          if (mounted) setState(() => _updateStatus = s);
        },
      );
    } catch (e) {
      if (mounted) setState(() => _updateStatus = 'Falhou: $e');
    } finally {
      if (mounted) setState(() => _downloading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return LuminusBackground(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(title: const Text('Ajustes')),
        body: ListView(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
          children: [
            const SectionHeader(title: 'Perfil'),
            _manaCard(),
            const SizedBox(height: 6),
            const SectionHeader(title: 'Leitura'),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Row(
                      children: [
                        const Text(
                          'Tamanho do texto',
                          style: TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const Spacer(),
                        Text(
                          '${(_textScale * 100).round()}%',
                          style: const TextStyle(color: kMuted, fontSize: 12),
                        ),
                      ],
                    ),
                    Slider(
                      value: _textScale,
                      min: .9,
                      max: 1.4,
                      divisions: 10,
                      label: '${(_textScale * 100).round()}%',
                      onChanged: _setTextScale,
                    ),
                    Container(
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: kSurface2,
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(color: kBorder),
                      ),
                      child: Text(
                        'A cena respira. O mundo lembra.',
                        style: storyStyle(size: 16 * _textScale, height: 1.6),
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 6),
            const SectionHeader(title: 'Conexão'),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      'Servidor do PC',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'Wi-Fi local ou Tailscale. O app tenta os dois automaticamente.',
                      style: TextStyle(
                        color: kMuted,
                        fontSize: 12.5,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: 14),
                    TextField(
                      controller: _url,
                      keyboardType: TextInputType.url,
                      decoration: const InputDecoration(
                        hintText: ApiClient.defaultServer,
                        prefixIcon: Icon(Icons.dns_outlined, size: 20),
                      ),
                    ),
                    const SizedBox(height: 12),
                    Row(
                      children: [
                        Expanded(
                          child: FilledButton(
                            onPressed: _save,
                            child: const Text('Salvar'),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: OutlinedButton(
                            onPressed: _test,
                            child: const Text('Testar'),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    OutlinedButton.icon(
                      onPressed: _discover,
                      icon: const Icon(Icons.wifi_find, size: 20),
                      label: const Text('Descobrir automaticamente'),
                    ),
                    if (_status.isNotEmpty) ...[
                      const SizedBox(height: 14),
                      Text(
                        _status,
                        style: const TextStyle(
                          color: kMuted,
                          fontSize: 12.5,
                          height: 1.4,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            const SectionHeader(title: 'Mundos e World Packs'),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      'Exportar / importar mundo',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'O pack leva mundo, elenco, lore e, se quiser, '
                      'as histórias com mensagens, capítulos e memória.',
                      style: TextStyle(
                        color: kMuted,
                        fontSize: 12.5,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: 14),
                    if (_loadingWorlds)
                      const LinearProgressIndicator(minHeight: 4)
                    else if (_worlds.isEmpty)
                      const Text(
                        'Nenhum mundo cadastrado ainda.',
                        style: TextStyle(color: kMuted, fontSize: 12.5),
                      )
                    else
                      DropdownButtonFormField<int>(
                        key: ValueKey('pack-world-$_worldId'),
                        initialValue: _worldId,
                        isExpanded: true,
                        decoration: const InputDecoration(
                          labelText: 'Mundo',
                          border: OutlineInputBorder(),
                        ),
                        items: _worlds
                            .map(
                              (w) => DropdownMenuItem(
                                value: w.id,
                                child: Text(
                                  w.name,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            )
                            .toList(),
                        onChanged: _packBusy
                            ? null
                            : (v) => setState(() => _worldId = v),
                      ),
                    const SizedBox(height: 10),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      value: _includeSessions,
                      onChanged: _packBusy
                          ? null
                          : (v) => setState(() => _includeSessions = v),
                      title: const Text(
                        'Incluir histórias',
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      subtitle: const Text(
                        'Mensagens, capítulos, elenco e memória de cada save.',
                        style: TextStyle(
                          fontSize: 11.5,
                          color: kMuted,
                          height: 1.4,
                        ),
                      ),
                    ),
                    const SizedBox(height: 6),
                    Row(
                      children: [
                        Expanded(
                          child: FilledButton.icon(
                            onPressed: _packBusy || _worldId == null
                                ? null
                                : _exportPack,
                            icon: const Icon(
                              Icons.upload_file_outlined,
                              size: 18,
                            ),
                            label: const Text('Exportar'),
                          ),
                        ),
                        const SizedBox(width: 10),
                        Expanded(
                          child: OutlinedButton.icon(
                            onPressed: _packBusy ? null : _importPack,
                            icon: const Icon(Icons.download_outlined, size: 18),
                            label: const Text('Importar'),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    OutlinedButton.icon(
                      onPressed: () => Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) =>
                              PlacesScreen(initialWorldId: _worldId),
                        ),
                      ),
                      icon: const Icon(Icons.place_outlined, size: 18),
                      label: const Text('Criar ambientes próprios'),
                    ),
                    if (_packBusy) ...[
                      const SizedBox(height: 10),
                      const LinearProgressIndicator(minHeight: 4),
                    ],
                    if (_packStatus.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Text(
                        _packStatus,
                        style: const TextStyle(
                          color: kMuted,
                          fontSize: 12,
                          height: 1.4,
                        ),
                      ),
                    ],
                    const SizedBox(height: 6),
                    const Text(
                      'A importação cria um mundo novo; nunca sobrescreve o original.',
                      style: TextStyle(
                        color: kMuted,
                        fontSize: 11,
                        height: 1.4,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            const SectionHeader(title: 'Atualizações'),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      'App no celular',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      _updateInfo,
                      style: const TextStyle(
                        color: kMuted,
                        fontSize: 12.5,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: 12),
                    if (_downloading) ...[
                      ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: LinearProgressIndicator(
                          value: _progress > 0 ? _progress : null,
                          minHeight: 8,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        '${(_progress * 100).toStringAsFixed(0)}%',
                        textAlign: TextAlign.center,
                        style: const TextStyle(color: kMuted, fontSize: 11.5),
                      ),
                      const SizedBox(height: 10),
                    ],
                    FilledButton.icon(
                      onPressed: _downloading ? null : _downloadAndInstall,
                      icon: const Icon(Icons.system_update_alt, size: 20),
                      label: Text(
                        _downloading
                            ? 'Baixando...'
                            : 'Baixar e instalar atualização',
                      ),
                    ),
                    const SizedBox(height: 8),
                    TextButton.icon(
                      onPressed: _downloading ? null : _loadUpdateInfo,
                      icon: const Icon(Icons.refresh, size: 18),
                      label: const Text('Verificar de novo'),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'O Android vai pedir para permitir a instalação de apps desconhecidos para o Luminus. '
                      'A atualização preserva o endereço do servidor salvo.',
                      style: TextStyle(
                        color: kMuted,
                        fontSize: 11,
                        height: 1.4,
                      ),
                    ),
                    if (_updateStatus.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Text(
                        _updateStatus,
                        style: const TextStyle(
                          color: kMuted,
                          fontSize: 12,
                          height: 1.4,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            const SectionHeader(title: 'Administração'),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Text(
                      'Apagar imagens',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'Opcional. Se LUMINUS_ADMIN_KEY estiver definida em '
                      'server/.env, use a mesma chave aqui. Sem chave, a '
                      'exclusão local de imagens fica liberada.',
                      style: TextStyle(
                        color: kMuted,
                        fontSize: 12.5,
                        height: 1.4,
                      ),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _adminKey,
                      obscureText: true,
                      autocorrect: false,
                      enableSuggestions: false,
                      decoration: const InputDecoration(
                        hintText: 'Chave de administrador',
                        prefixIcon: Icon(Icons.key_outlined, size: 20),
                      ),
                    ),
                    const SizedBox(height: 10),
                    FilledButton.icon(
                      onPressed: _saveAdminKey,
                      icon: const Icon(Icons.lock_outline, size: 18),
                      label: const Text('Salvar chave'),
                    ),
                    if (_adminStatus.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Text(
                        _adminStatus,
                        style: const TextStyle(color: kMuted, fontSize: 12.5),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            const SectionHeader(title: 'Sobre'),
            Card(
              child: Column(
                children: [
                  ListTile(
                    leading: const Icon(Icons.auto_stories, color: kPrimary),
                    title: const Text('Luminus'),
                    subtitle: const Text('RPG narrado por IA local'),
                  ),
                  const Divider(height: 1),
                  ListTile(
                    leading: const Icon(Icons.bolt_outlined, color: kAccent),
                    title: const Text('Servidor atual'),
                    subtitle: Text(
                      ApiClient.baseUrl,
                      style: const TextStyle(color: kMuted, fontSize: 12),
                    ),
                  ),
                  const Divider(height: 1),
                  ListTile(
                    leading: const Icon(Icons.auto_awesome, color: kPrimary),
                    title: const Text('Capas com IA'),
                    subtitle: const Text(
                      'DeepSeek cria o prompt; o servidor renderiza a imagem.',
                      style: TextStyle(color: kMuted, fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 18),
            const Center(
              child: Text(
                'Ícones: game-icons.net (CC BY 3.0) · Molduras: Kenney (CC0)',
                textAlign: TextAlign.center,
                style: TextStyle(color: kMuted, fontSize: 10.5),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

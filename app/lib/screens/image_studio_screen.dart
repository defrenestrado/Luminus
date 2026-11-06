import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../theme.dart';

class ImageStudioScreen extends StatefulWidget {
  const ImageStudioScreen({super.key});

  @override
  State<ImageStudioScreen> createState() => _ImageStudioScreenState();
}

class _ImageStudioScreenState extends State<ImageStudioScreen> {
  final _prompt = TextEditingController();
  final _scroll = ScrollController();

  String _style = 'anime';
  int _width = 1024;
  int _height = 1024;
  bool _enhance = true;
  bool _adult = false;
  String _adultModel = 'anime';
  bool _generating = false;
  bool _loadingGallery = true;
  String _status = '';
  String _error = '';
  String _imageUrl = '';
  String _usedPrompt = '';
  String _promptSource = '';
  int _resultWidth = 1024;
  int _resultHeight = 1024;
  List<Map<String, dynamic>> _gallery = const [];

  static const _styles = [
    ('anime', 'Anime'),
    ('pintura', 'Pintura'),
    ('grimdark', 'Grimdark'),
    ('aquarela', 'Aquarela'),
  ];

  static const _sizes = [
    ('Quadrado', 1024, 1024),
    ('Retrato', 768, 1024),
    ('Cena', 1280, 720),
  ];

  @override
  void initState() {
    super.initState();
    _loadGallery();
  }

  @override
  void dispose() {
    _prompt.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _loadGallery() async {
    if (mounted) setState(() => _loadingGallery = true);
    try {
      final items = await ApiClient.artGallery(limit: 60);
      if (!mounted) return;
      setState(() {
        _gallery = items;
        _loadingGallery = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loadingGallery = false);
    }
  }

  Future<void> _generate() async {
    if (_generating) return;
    final text = _prompt.text.trim();
    if (text.isEmpty) {
      setState(() => _error = 'Descreva a imagem que você quer.');
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() {
      _generating = true;
      _error = '';
      _imageUrl = '';
      _usedPrompt = '';
      _promptSource = '';
      _status = 'Pintando na RTX 2060...';
    });
    try {
      final data = await ApiClient.generateImage(
        prompt: text,
        style: _style,
        width: _width,
        height: _height,
        enhance: _enhance,
        adult: _adult,
        model: _adult ? _adultModel : '',
      );
      if (!mounted) return;
      setState(() {
        _imageUrl = (data['url'] ?? '').toString();
        _usedPrompt = (data['prompt'] ?? '').toString();
        _promptSource = (data['prompt_source'] ?? '').toString();
        _resultWidth = (data['width'] as num?)?.toInt() ?? _width;
        _resultHeight = (data['height'] as num?)?.toInt() ?? _height;
        final warning = (data['warning'] ?? '').toString();
        _status = warning.isNotEmpty
            ? warning
            : 'Pronto. O narrador já foi recarregado.';
      });
      _loadGallery();
      if (_scroll.hasClients) {
        _scroll.animateTo(
          0,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _status = '';
          _error = e is String ? e : ApiClient.friendly(e);
        });
      }
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  Future<void> _copyLink() async {
    if (_imageUrl.isEmpty) return;
    await Clipboard.setData(
      ClipboardData(text: ApiClient.mediaUrl(_imageUrl)),
    );
    _snack('Link copiado.');
  }

  void _openImage(String url) {
    if (url.isEmpty) return;
    Map<String, dynamic>? item;
    for (final entry in _gallery) {
      if ((entry['url'] ?? '').toString() == url) {
        item = entry;
        break;
      }
    }
    showDialog<void>(
      context: context,
      builder: (dialogContext) => Dialog(
        backgroundColor: kSurface,
        insetPadding: const EdgeInsets.all(12),
        clipBehavior: Clip.antiAlias,
        child: Stack(
          children: [
            SizedBox(
              width: MediaQuery.sizeOf(dialogContext).width * .92,
              height: MediaQuery.sizeOf(dialogContext).height * .8,
              child: InteractiveViewer(
                child: Image.network(
                  ApiClient.mediaUrl(url),
                  fit: BoxFit.contain,
                ),
              ),
            ),
            Positioned(
              top: 4,
              left: 4,
              child: item == null
                  ? const SizedBox.shrink()
                  : IconButton(
                      tooltip: 'Apagar imagem',
                      style: IconButton.styleFrom(
                        backgroundColor: Colors.black.withValues(alpha: .55),
                      ),
                      onPressed: () {
                        Navigator.pop(dialogContext);
                        _deleteImage(item!);
                      },
                      icon: const Icon(Icons.delete_outline),
                    ),
            ),
            Positioned(
              top: 4,
              right: 4,
              child: IconButton(
                style: IconButton.styleFrom(
                  backgroundColor: Colors.black.withValues(alpha: .55),
                ),
                onPressed: () => Navigator.pop(dialogContext),
                icon: const Icon(Icons.close),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _deleteImage(Map<String, dynamic> item) async {
    final name = (item['name'] ?? '').toString();
    if (name.isEmpty) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('Apagar imagem?'),
        content: Text(
          'A imagem "$name" será removida do servidor. '
          'Essa ação não pode ser desfeita.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancelar'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kDanger),
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Apagar'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await ApiClient.deleteArt(name);
      if (!mounted) return;
      setState(() {
        _gallery = _gallery.where((e) => e['name'] != name).toList();
        if (_imageUrl.endsWith(name)) {
          _imageUrl = '';
          _usedPrompt = '';
          _promptSource = '';
          _error = '';
        }
      });
      _snack('Imagem apagada.');
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

  String _when(dynamic value) {
    final ts = (value as num?)?.toDouble() ?? 0;
    if (ts <= 0) return '';
    final date = DateTime.fromMillisecondsSinceEpoch((ts * 1000).round());
    final day = date.day.toString().padLeft(2, '0');
    final month = date.month.toString().padLeft(2, '0');
    final hour = date.hour.toString().padLeft(2, '0');
    final minute = date.minute.toString().padLeft(2, '0');
    return '$day/$month $hour:$minute';
  }

  Widget _promptCard() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: kBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text(
            'O que você quer ver?',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 6),
          const Text(
            'Descreva a cena, o personagem, o clima. A imagem é pintada na RTX 2060. '
            'Dica: inglês costuma render melhor.',
            style: TextStyle(color: kMuted, fontSize: 12.5, height: 1.4),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _prompt,
            minLines: 3,
            maxLines: 6,
            textCapitalization: TextCapitalization.sentences,
            decoration: InputDecoration(
              hintText: _adult
                  ? (_adultModel == 'photoreal'
                      ? 'Ex.: retrato fotorrealista de uma mulher adulta em uma taverna...'
                      : 'Ex.: cena adulta consensual entre personagens adultos...')
                  : 'Ex.: uma cidade flutuante ao amanhecer, ...',
              border: const OutlineInputBorder(),
            ),
          ),
          if (!(_adult && _adultModel == 'photoreal')) ...[
            const SizedBox(height: 14),
            const Text('Estilo',
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final style in _styles)
                  ChoiceChip(
                    label: Text(style.$2),
                    selected: _style == style.$1,
                    onSelected: (_) => setState(() => _style = style.$1),
                  ),
              ],
            ),
          ],
          const SizedBox(height: 12),
          const Text('Formato',
              style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final size in _sizes)
                ChoiceChip(
                  label: Text(size.$1),
                  selected: _width == size.$2 && _height == size.$3,
                  onSelected: (_) => setState(() {
                    _width = size.$2;
                    _height = size.$3;
                  }),
                ),
            ],
          ),
          const SizedBox(height: 6),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: _adult,
            onChanged: (value) => setState(() => _adult = value),
            title: const Text('Modo adulto (sem censura local)',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            subtitle: const Text(
              'SDXL local. Somente personagens adultos e conteúdo consensual; sem pessoas reais.',
              style: TextStyle(color: kMuted, fontSize: 11.5, height: 1.35),
            ),
          ),
          if (_adult) ...[
            Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: kDanger.withValues(alpha: .08),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: kDanger.withValues(alpha: .3)),
              ),
              child: const Text(
                'Conteúdo adulto: todos os personagens devem ser adultos e a cena consensual. '
                'Não descreva menores, pessoas reais ou não-consensual.',
                style: TextStyle(fontSize: 11, height: 1.4),
              ),
            ),
            const Text('Modelo adulto',
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                ChoiceChip(
                  label: const Text('Anime (NoobAI)'),
                  selected: _adultModel == 'anime',
                  onSelected: (_) => setState(() => _adultModel = 'anime'),
                ),
                ChoiceChip(
                  label: const Text('Fotorrealista (Lustify)'),
                  selected: _adultModel == 'photoreal',
                  onSelected: (_) => setState(() => _adultModel = 'photoreal'),
                ),
              ],
            ),
            const SizedBox(height: 8),
          ],
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            value: _enhance,
            onChanged: (value) => setState(() => _enhance = value),
            title: const Text('Melhorar prompt com IA',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600)),
            subtitle: const Text(
              'Usa a DeepSeek quando a chave está configurada.',
              style: TextStyle(color: kMuted, fontSize: 11.5),
            ),
          ),
          const SizedBox(height: 4),
          FilledButton.icon(
            onPressed: _generating ? null : _generate,
            icon: _generating
                ? const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.auto_awesome),
            label: Text(_generating ? 'Pintando...' : 'Gerar imagem'),
          ),
          const SizedBox(height: 8),
          const Text(
            'Pode levar alguns minutos. O 9B é descarregado durante a pintura '
            'e recarregado antes de voltar ao jogo.',
            style: TextStyle(color: kMuted, fontSize: 11, height: 1.4),
          ),
        ],
      ),
    );
  }

  Widget _loadingCard() {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: kBorder),
      ),
      child: Column(
        children: [
          const SizedBox(
            width: 34,
            height: 34,
            child: CircularProgressIndicator(strokeWidth: 3),
          ),
          const SizedBox(height: 12),
          Text(
            _status.isEmpty ? 'Pintando...' : _status,
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 13.5, height: 1.4),
          ),
          const SizedBox(height: 6),
          const Text(
            'O narrador 9B volta a carregar ao terminar. O jogo não fica travado.',
            textAlign: TextAlign.center,
            style: TextStyle(color: kMuted, fontSize: 11.5, height: 1.4),
          ),
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: const LinearProgressIndicator(minHeight: 4),
          ),
        ],
      ),
    );
  }

  Widget _errorCard() {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 8, 8, 8),
      decoration: BoxDecoration(
        color: kDanger.withValues(alpha: .1),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: kDanger.withValues(alpha: .35)),
      ),
      child: Row(
        children: [
          const Icon(Icons.error_outline, size: 18, color: kDanger),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              _error,
              style: const TextStyle(fontSize: 12, height: 1.4),
            ),
          ),
          TextButton(
            onPressed: _generate,
            child: const Text('Tentar'),
          ),
        ],
      ),
    );
  }

  Widget _resultCard() {
    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: kBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          GestureDetector(
            onTap: () => _openImage(_imageUrl),
            child: AspectRatio(
              aspectRatio: _resultWidth / _resultHeight,
              child: Image.network(
                ApiClient.mediaUrl(_imageUrl),
                fit: BoxFit.cover,
                loadingBuilder: (_, child, progress) => progress == null
                    ? child
                    : const Center(child: CircularProgressIndicator()),
                errorBuilder: (_, _, _) => const SizedBox(
                  height: 180,
                  child: Center(child: Icon(Icons.broken_image_outlined)),
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 6, 6, 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Expanded(
                      child: Text(
                        'Imagem gerada',
                        style: TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    IconButton(
                      tooltip: 'Copiar link',
                      onPressed: _copyLink,
                      icon: const Icon(Icons.link, size: 20),
                    ),
                    IconButton(
                      tooltip: 'Abrir',
                      onPressed: () => _openImage(_imageUrl),
                      icon: const Icon(Icons.open_in_full, size: 19),
                    ),
                  ],
                ),
                if (_usedPrompt.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: Text(
                      'Prompt: $_usedPrompt',
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: kMuted,
                        fontSize: 11.5,
                        height: 1.4,
                      ),
                    ),
                  ),
                if (_promptSource.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 4, right: 8),
                    child: Text(
                      'Prompt: $_promptSource',
                      style: const TextStyle(color: kMuted, fontSize: 10.5),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _emptyGallery() {
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: kSurface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: kBorder),
      ),
      child: const Column(
        children: [
          Icon(Icons.image_outlined, size: 38, color: kPrimary),
          SizedBox(height: 10),
          Text(
            'Nenhuma imagem ainda',
            style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700),
          ),
          SizedBox(height: 4),
          Text(
            'Gere a primeira acima. Ela aparece aqui.',
            textAlign: TextAlign.center,
            style: TextStyle(color: kMuted, fontSize: 12, height: 1.4),
          ),
        ],
      ),
    );
  }

  Widget _galleryGrid() {
    return GridView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        mainAxisSpacing: 10,
        crossAxisSpacing: 10,
        childAspectRatio: .95,
      ),
      itemCount: _gallery.length,
      itemBuilder: (_, index) {
        final item = _gallery[index];
        final url = (item['url'] ?? '').toString();
        return Material(
          color: kSurface,
          borderRadius: BorderRadius.circular(16),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: () => _openImage(url),
            onLongPress: () => _deleteImage(item),
            child: Stack(
              fit: StackFit.expand,
              children: [
                Image.network(
                  ApiClient.mediaUrl(url),
                  fit: BoxFit.cover,
                  loadingBuilder: (_, child, progress) => progress == null
                      ? child
                      : Container(color: kSurface2),
                  errorBuilder: (_, _, _) => const Center(
                    child: Icon(Icons.broken_image_outlined, color: kMuted),
                  ),
                ),
                Positioned(
                  left: 0,
                  right: 0,
                  bottom: 0,
                  child: Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.topCenter,
                        end: Alignment.bottomCenter,
                        colors: [
                          Colors.transparent,
                          Colors.black.withValues(alpha: .78),
                        ],
                      ),
                    ),
                    child: Text(
                      _when(item['created_at']),
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 10,
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
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
            children: const [
              Text(
                'Estúdio',
                style: TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w800,
                  letterSpacing: .4,
                ),
              ),
              Text(
                'Crie a imagem que quiser',
                style: TextStyle(
                  color: kMuted,
                  fontSize: 11.5,
                  fontWeight: FontWeight.w400,
                ),
              ),
            ],
          ),
        ),
        body: ListView(
          controller: _scroll,
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 110),
          children: [
            _promptCard(),
            if (_generating) ...[
              const SizedBox(height: 16),
              _loadingCard(),
            ],
            if (_error.isNotEmpty && !_generating) ...[
              const SizedBox(height: 12),
              _errorCard(),
            ],
            if (_imageUrl.isNotEmpty && !_generating) ...[
              const SizedBox(height: 16),
              _resultCard(),
            ],
            const SizedBox(height: 24),
            SectionHeader(
              title: 'Suas imagens',
              action: 'Atualizar',
              onAction: _loadGallery,
            ),
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text(
                'Toque para abrir e use a lixeira, ou segure para apagar.',
                style: TextStyle(color: kMuted, fontSize: 11),
              ),
            ),
            if (_loadingGallery)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 24),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (_gallery.isEmpty)
              _emptyGallery()
            else
              _galleryGrid(),
          ],
        ),
      ),
    );
  }
}

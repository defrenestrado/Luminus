import 'package:flutter/material.dart';

import '../api.dart';
import '../theme.dart';

class PlacesScreen extends StatefulWidget {
  final int? initialWorldId;
  const PlacesScreen({super.key, this.initialWorldId});

  @override
  State<PlacesScreen> createState() => _PlacesScreenState();
}

class _PlacesScreenState extends State<PlacesScreen> {
  List<Place> _places = [];
  List<World> _worlds = [];
  int? _worldId;
  bool _loading = true;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _worldId = widget.initialWorldId;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      final results = await Future.wait([
        ApiClient.worlds(),
        ApiClient.places(worldId: _worldId),
      ]);
      if (!mounted) return;
      setState(() {
        _worlds = results[0] as List<World>;
        _places = results[1] as List<Place>;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is String ? e : ApiClient.friendly(e);
      });
    }
  }

  String _worldName(int? id) {
    for (final world in _worlds) {
      if (world.id == id) return world.name;
    }
    return 'Sem mundo';
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  Future<void> _edit([Place? place]) async {
    final name = TextEditingController(text: place?.name ?? '');
    final kind = TextEditingController(text: place?.kind ?? 'ambiente');
    final description = TextEditingController(text: place?.description ?? '');
    final atmosphere = TextEditingController(text: place?.atmosphere ?? '');
    final rules = TextEditingController(text: place?.rules ?? '');
    final tags = TextEditingController(text: (place?.tags ?? []).join(', '));
    int? worldId = place?.worldId ?? _worldId;
    String error = '';

    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          backgroundColor: kSurface,
          title: Text(place == null ? 'Novo ambiente' : 'Editar ambiente'),
          content: SizedBox(
            width: 520,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextField(
                    controller: name,
                    decoration: const InputDecoration(
                      labelText: 'Nome',
                      hintText: 'Ex.: Estação do Midgrid',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: kind,
                    decoration: const InputDecoration(
                      labelText: 'Tipo',
                      hintText: 'cidade, nave, taverna, ruína...',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 10),
                  DropdownButtonFormField<int?>(
                    initialValue: worldId,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Mundo',
                      border: OutlineInputBorder(),
                    ),
                    items: [
                      const DropdownMenuItem<int?>(
                        value: null,
                        child: Text('Sem mundo (avulso)'),
                      ),
                      for (final world in _worlds)
                        DropdownMenuItem<int?>(
                          value: world.id,
                          child: Text(
                            world.name,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: (value) => setDialogState(() => worldId = value),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: description,
                    minLines: 2,
                    maxLines: 4,
                    decoration: const InputDecoration(
                      labelText: 'Descrição',
                      hintText: 'O que o jogador vê e ouve ao chegar?',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: atmosphere,
                    minLines: 2,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'Atmosfera',
                      hintText: 'Luz, cheiro, som, clima, tensão do lugar.',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: rules,
                    minLines: 2,
                    maxLines: 3,
                    decoration: const InputDecoration(
                      labelText: 'Regras do lugar',
                      hintText:
                          'Quem manda, o que é proibido, quem cobra o quê.',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: tags,
                    decoration: const InputDecoration(
                      labelText: 'Tags',
                      hintText: 'separadas por vírgula',
                      border: OutlineInputBorder(),
                    ),
                  ),
                  if (error.isNotEmpty) ...[
                    const SizedBox(height: 10),
                    Text(
                      error,
                      style: const TextStyle(color: kDanger, fontSize: 12),
                    ),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancelar'),
            ),
            FilledButton(
              onPressed: () async {
                if (name.text.trim().isEmpty) {
                  setDialogState(() => error = 'Dê um nome ao ambiente.');
                  return;
                }
                try {
                  final tagList = tags.text
                      .split(',')
                      .map((e) => e.trim())
                      .where((e) => e.isNotEmpty)
                      .toList();
                  if (place == null) {
                    await ApiClient.createPlace(
                      worldId: worldId,
                      name: name.text.trim(),
                      kind: kind.text.trim(),
                      description: description.text.trim(),
                      atmosphere: atmosphere.text.trim(),
                      rules: rules.text.trim(),
                      tags: tagList,
                    );
                  } else {
                    await ApiClient.updatePlace(
                      place.id,
                      worldId: worldId,
                      name: name.text.trim(),
                      kind: kind.text.trim(),
                      description: description.text.trim(),
                      atmosphere: atmosphere.text.trim(),
                      rules: rules.text.trim(),
                      tags: tagList,
                    );
                  }
                  if (context.mounted) Navigator.pop(context, true);
                } catch (e) {
                  setDialogState(() {
                    error = e is String ? e : ApiClient.friendly(e);
                  });
                }
              },
              child: const Text('Salvar'),
            ),
          ],
        ),
      ),
    );
    if (saved == true) _load();
  }

  Future<void> _delete(Place place) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        backgroundColor: kSurface,
        title: const Text('Apagar ambiente?'),
        content: Text(place.name),
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
    if (ok != true) return;
    try {
      await ApiClient.deletePlace(place.id);
      _load();
    } catch (e) {
      _snack(e is String ? e : ApiClient.friendly(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    return LuminusBackground(
      child: Scaffold(
        backgroundColor: Colors.transparent,
        appBar: AppBar(title: const Text('Ambientes')),
        floatingActionButton: FloatingActionButton.extended(
          onPressed: () => _edit(),
          icon: const Icon(Icons.add_location_alt_outlined),
          label: const Text('Novo ambiente'),
        ),
        body: RefreshIndicator(
          onRefresh: _load,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
            children: [
              DropdownButtonFormField<int?>(
                initialValue: _worldId,
                isExpanded: true,
                decoration: const InputDecoration(
                  labelText: 'Filtrar por mundo',
                  border: OutlineInputBorder(),
                ),
                items: [
                  const DropdownMenuItem<int?>(
                    value: null,
                    child: Text('Todos os mundos'),
                  ),
                  for (final world in _worlds)
                    DropdownMenuItem<int?>(
                      value: world.id,
                      child: Text(world.name, overflow: TextOverflow.ellipsis),
                    ),
                ],
                onChanged: (value) {
                  setState(() => _worldId = value);
                  _load();
                },
              ),
              const SizedBox(height: 14),
              if (_loading)
                const Padding(
                  padding: EdgeInsets.only(top: 60),
                  child: Center(child: CircularProgressIndicator()),
                )
              else if (_error.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 40),
                  child: Text(
                    _error,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: kDanger),
                  ),
                )
              else if (_places.isEmpty)
                const Padding(
                  padding: EdgeInsets.only(top: 60),
                  child: Column(
                    children: [
                      Icon(Icons.landscape_outlined, size: 46, color: kMuted),
                      SizedBox(height: 12),
                      Text(
                        'Nenhum ambiente criado ainda.\n'
                        'Crie um lugar próprio para usar como base da história.',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: kMuted, height: 1.5),
                      ),
                    ],
                  ),
                )
              else
                for (final place in _places) _placeCard(place),
            ],
          ),
        ),
      ),
    );
  }

  Widget _placeCard(Place place) {
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 42,
              height: 42,
              decoration: BoxDecoration(
                color: kPrimary.withValues(alpha: .16),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: kBorder),
              ),
              child: const Icon(Icons.place_outlined, color: kAccent),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    place.name,
                    style: const TextStyle(
                      fontWeight: FontWeight.w700,
                      fontSize: 15,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    '${place.kind.isEmpty ? 'ambiente' : place.kind} · '
                    '${_worldName(place.worldId)}',
                    style: const TextStyle(color: kMuted, fontSize: 11.5),
                  ),
                  if (place.description.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(
                      place.description,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: kMuted,
                        fontSize: 12.5,
                        height: 1.4,
                      ),
                    ),
                  ],
                  if (place.tags.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: [
                        for (final tag in place.tags.take(4))
                          Chip(
                            label: Text(
                              tag,
                              style: const TextStyle(fontSize: 10.5),
                            ),
                            visualDensity: VisualDensity.compact,
                            materialTapTargetSize:
                                MaterialTapTargetSize.shrinkWrap,
                          ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
            PopupMenuButton<String>(
              onSelected: (value) {
                if (value == 'edit') _edit(place);
                if (value == 'delete') _delete(place);
              },
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'edit', child: Text('Editar')),
                PopupMenuItem(value: 'delete', child: Text('Apagar')),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

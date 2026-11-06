import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

class DiceRollOverlay extends StatefulWidget {
  final Map<String, dynamic> receipt;
  final VoidCallback onDismiss;

  const DiceRollOverlay({
    super.key,
    required this.receipt,
    required this.onDismiss,
  });

  @override
  State<DiceRollOverlay> createState() => _DiceRollOverlayState();
}

class _DiceRollOverlayState extends State<DiceRollOverlay>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  )..forward();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  int _int(dynamic value) => (value as num?)?.toInt() ?? 0;

  String _str(dynamic value) => (value ?? '').toString();

  Color _color(String outcome) {
    switch (outcome) {
      case 'sucesso_critico':
      case 'sucesso':
        return kAccent;
      case 'parcial':
        return kPrimary;
      default:
        return kDanger;
    }
  }

  @override
  Widget build(BuildContext context) {
    final receipt = widget.receipt;
    final outcome = _str(receipt['outcome']);
    final color = _color(outcome);
    final label = _str(receipt['label']);
    final outcomeLabel = _str(receipt['outcome_label']);
    final roll = _int(receipt['roll']);
    final modifier = _int(receipt['modifier']);
    final total = _int(receipt['total']);
    final dc = _int(receipt['dc']);
    final consequence = _str(receipt['consequences']).trim();

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: widget.onDismiss,
      child: ColoredBox(
        color: Colors.black.withValues(alpha: .74),
        child: Center(
          child: Container(
            margin: const EdgeInsets.all(28),
            padding: const EdgeInsets.fromLTRB(24, 22, 24, 18),
            decoration: BoxDecoration(
              color: kSurface,
              borderRadius: BorderRadius.circular(26),
              border: Border.all(color: color.withValues(alpha: .55)),
              boxShadow: [
                BoxShadow(
                  color: color.withValues(alpha: .18),
                  blurRadius: 34,
                  spreadRadius: 2,
                ),
              ],
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  'TESTE DE ${label.toUpperCase()}',
                  style: const TextStyle(
                    color: kMuted,
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    letterSpacing: 1.5,
                  ),
                ),
                const SizedBox(height: 16),
                AnimatedBuilder(
                  animation: _controller,
                  builder: (_, _) {
                    final t =
                        Curves.easeOutCubic.transform(_controller.value);
                    final angle = (1 - t) * math.pi * 4;
                    return Transform.rotate(
                      angle: angle,
                      child: Container(
                        width: 96,
                        height: 96,
                        alignment: Alignment.center,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          gradient: const LinearGradient(
                            begin: Alignment.topLeft,
                            end: Alignment.bottomRight,
                            colors: [kSurface2, kBg],
                          ),
                          border: Border.all(
                            color: color.withValues(alpha: .65),
                            width: 2,
                          ),
                          boxShadow: [
                            BoxShadow(
                              color: color.withValues(alpha: .28),
                              blurRadius: 26,
                            ),
                          ],
                        ),
                        child: Text(
                          '$roll',
                          style: TextStyle(
                            fontSize: 34,
                            fontWeight: FontWeight.w800,
                            color: color,
                            height: 1,
                          ),
                        ),
                      ),
                    );
                  },
                ),
                const SizedBox(height: 14),
                Text(
                  outcomeLabel,
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: color,
                    fontSize: 18,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 5),
                Text(
                  'd20 $roll ${modifier >= 0 ? '+' : ''}$modifier = $total  ·  CD $dc',
                  style: const TextStyle(
                    color: kMuted,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                if (consequence.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Text(
                    consequence,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: kMuted,
                      fontSize: 11.5,
                      height: 1.4,
                    ),
                  ),
                ],
                const SizedBox(height: 12),
                const Text(
                  'Toque para continuar',
                  style: TextStyle(color: kMuted, fontSize: 10.5),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

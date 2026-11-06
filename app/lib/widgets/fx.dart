import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

class GlowPulse extends StatefulWidget {
  final Widget child;
  final Color color;
  final double blur;
  final double radius;

  const GlowPulse({
    super.key,
    required this.child,
    this.color = kPrimary,
    this.blur = 18,
    this.radius = 16,
  });

  @override
  State<GlowPulse> createState() => _GlowPulseState();
}

class _GlowPulseState extends State<GlowPulse>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 3),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (_, child) {
        final t = Curves.easeInOut.transform(_controller.value);
        return DecoratedBox(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(widget.radius),
            boxShadow: [
              BoxShadow(
                color: widget.color.withValues(alpha: .10 + .22 * t),
                blurRadius: widget.blur * (.7 + .6 * t),
                spreadRadius: 1 + 2.5 * t,
              ),
            ],
          ),
          child: child,
        );
      },
      child: widget.child,
    );
  }
}

class EmberField extends StatefulWidget {
  final int count;
  final Color color;

  const EmberField({
    super.key,
    this.count = 14,
    this.color = kAccent,
  });

  @override
  State<EmberField> createState() => _EmberFieldState();
}

class _EmberFieldState extends State<EmberField>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 7),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Widget _ember(int index, double t, Size size) {
    final phase = (t + index / widget.count) % 1.0;
    final rnd = math.Random(index * 97 + 13);
    final x = rnd.nextDouble() * size.width;
    final drift = math.sin(phase * math.pi * 2 + index * 1.7) * 16;
    final y = size.height - phase * size.height * .92;
    final dot = 1.4 + rnd.nextDouble() * 2.4;
    final opacity = (math.sin(phase * math.pi) * .8).clamp(0.0, .8);
    return Positioned(
      left: x + drift,
      top: y,
      child: Opacity(
        opacity: opacity,
        child: Container(
          width: dot,
          height: dot,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: widget.color,
            boxShadow: [
              BoxShadow(
                color: widget.color.withValues(alpha: .7),
                blurRadius: 6,
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final size = Size(constraints.maxWidth, constraints.maxHeight);
          return AnimatedBuilder(
            animation: _controller,
            builder: (_, _) {
              final t = _controller.value;
              return Stack(
                children: [
                  for (var i = 0; i < widget.count; i++) _ember(i, t, size),
                ],
              );
            },
          );
        },
      ),
    );
  }
}

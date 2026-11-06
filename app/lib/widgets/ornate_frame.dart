import 'package:flutter/material.dart';

import '../theme.dart';

class OrnateFrame extends StatelessWidget {
  final Widget child;
  final String asset;
  final Color color;
  final Color fill;
  final double border;
  final Rect slice;

  const OrnateFrame({
    super.key,
    required this.child,
    this.asset = 'assets/images/ui/frame_014.png',
    this.color = kAccent,
    this.fill = const Color(0xE608070F),
    this.border = 12,
    this.slice = const Rect.fromLTRB(16, 16, 80, 80),
  });

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        Positioned.fill(
          child: Image.asset(
            asset,
            fit: BoxFit.fill,
            centerSlice: slice,
            color: color,
            colorBlendMode: BlendMode.srcIn,
            filterQuality: FilterQuality.none,
          ),
        ),
        Positioned.fill(
          child: Padding(
            padding: EdgeInsets.all(border),
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: fill,
                borderRadius: BorderRadius.circular(10),
                boxShadow: [
                  BoxShadow(
                    color: color.withValues(alpha: .10),
                    blurRadius: 18,
                    spreadRadius: 1,
                  ),
                ],
              ),
            ),
          ),
        ),
        Padding(
          padding: EdgeInsets.fromLTRB(border + 8, border + 4, border + 8, border + 2),
          child: child,
        ),
      ],
    );
  }
}

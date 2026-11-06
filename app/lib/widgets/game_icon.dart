import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';

class GameIcon extends StatelessWidget {
  final String name;
  final double size;
  final Color color;

  const GameIcon(
    this.name, {
    super.key,
    this.size = 20,
    this.color = Colors.white,
  });

  @override
  Widget build(BuildContext context) {
    return SvgPicture.asset(
      'assets/icons/$name.svg',
      width: size,
      height: size,
      colorFilter: ColorFilter.mode(color, BlendMode.srcIn),
    );
  }
}

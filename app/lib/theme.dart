import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'api.dart';

const kBg = Color(0xFF08070F);
const kSurface = Color(0xFF141127);
const kSurface2 = Color(0xFF1D1838);
const kBorder = Color(0xFF2B2450);
const kPrimary = Color(0xFF8B7CF6);
const kPrimaryDeep = Color(0xFF6C5CE7);
const kAccent = Color(0xFFF2C879);
const kText = Color(0xFFF3F0FF);
const kMuted = Color(0xFF9C93B8);
const kDanger = Color(0xFFE56A6A);

const kSans = 'Manrope';
const kSerif = 'Lora';

TextStyle storyStyle({
  Color color = kText,
  double size = 16,
  FontWeight weight = FontWeight.w400,
  double height = 1.58,
}) {
  return TextStyle(
    fontFamily: kSerif,
    fontSize: size,
    height: height,
    fontWeight: weight,
    color: color,
  );
}

ThemeData buildLuminusTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: kPrimary,
    brightness: Brightness.dark,
  ).copyWith(
    primary: kPrimary,
    secondary: kAccent,
    surface: kSurface,
    error: kDanger,
  );
  final base = ThemeData(
    brightness: Brightness.dark,
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: kBg,
    fontFamily: kSans,
  );
  return base.copyWith(
    appBarTheme: const AppBarTheme(
      backgroundColor: Colors.transparent,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      centerTitle: false,
      iconTheme: IconThemeData(color: kText),
      titleTextStyle: TextStyle(
        color: kText,
        fontFamily: kSans,
        fontSize: 20,
        fontWeight: FontWeight.w700,
        letterSpacing: .3,
      ),
    ),
    cardTheme: CardThemeData(
      color: kSurface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(20),
        side: const BorderSide(color: kBorder),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: kSurface2,
      hintStyle: const TextStyle(color: kMuted),
      labelStyle: const TextStyle(color: kMuted),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: const BorderSide(color: kBorder),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: const BorderSide(color: kBorder),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(16),
        borderSide: const BorderSide(color: kPrimary, width: 1.4),
      ),
    ),
    chipTheme: base.chipTheme.copyWith(
      backgroundColor: kSurface2,
      selectedColor: kPrimary.withValues(alpha: .28),
      side: const BorderSide(color: kBorder),
      labelStyle: const TextStyle(
        color: kText,
        fontFamily: kSans,
        fontSize: 12,
      ),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
    ),
    dividerTheme: const DividerThemeData(color: kBorder, thickness: 1),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: kSurface,
      elevation: 0,
      height: 68,
      indicatorColor: kPrimary.withValues(alpha: .2),
      labelTextStyle: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return TextStyle(
          fontFamily: kSans,
          fontSize: 11,
          fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
          color: selected ? kText : kMuted,
        );
      }),
      iconTheme: WidgetStateProperty.resolveWith((states) {
        final selected = states.contains(WidgetState.selected);
        return IconThemeData(
          size: 22,
          color: selected ? kAccent : kMuted,
        );
      }),
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: kSurface2,
      contentTextStyle: const TextStyle(color: kText, fontFamily: kSans),
      behavior: SnackBarBehavior.floating,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: kPrimary,
        foregroundColor: Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
        textStyle: const TextStyle(
          fontFamily: kSans,
          fontWeight: FontWeight.w600,
        ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: kText,
        side: const BorderSide(color: kBorder),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
      ),
    ),
    textTheme: base.textTheme
        .apply(bodyColor: kText, displayColor: kText, fontFamily: kSans)
        .copyWith(
          titleLarge: const TextStyle(
            color: kText,
            fontWeight: FontWeight.w700,
            letterSpacing: .2,
          ),
          titleMedium: const TextStyle(color: kText, fontWeight: FontWeight.w600),
          bodySmall: const TextStyle(color: kMuted, height: 1.4),
          labelSmall: const TextStyle(color: kMuted, letterSpacing: .6),
        ),
  );
}

class LuminusBackground extends StatelessWidget {
  final Widget child;
  const LuminusBackground({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        gradient: RadialGradient(
          center: Alignment(-.6, -.85),
          radius: 1.35,
          colors: [Color(0xFF1B1442), kBg],
          stops: [0, .72],
        ),
      ),
      child: Stack(
        children: [
          const Positioned.fill(
            child: IgnorePointer(
              child: CustomPaint(painter: _StarfieldPainter()),
            ),
          ),
          child,
        ],
      ),
    );
  }
}

class _StarfieldPainter extends CustomPainter {
  const _StarfieldPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final rnd = math.Random(7);
    final paint = Paint();
    for (var i = 0; i < 110; i++) {
      final x = rnd.nextDouble() * size.width;
      final y = rnd.nextDouble() * size.height;
      final radius = .4 + rnd.nextDouble() * 1.3;
      final alpha = .05 + rnd.nextDouble() * .22;
      paint.color = Colors.white.withValues(alpha: alpha);
      canvas.drawCircle(Offset(x, y), radius, paint);
      if (i % 17 == 0) {
        paint.color = kAccent.withValues(alpha: .28);
        canvas.drawCircle(Offset(x, y), radius * 1.8, paint);
      }
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class SectionHeader extends StatelessWidget {
  final String title;
  final String? action;
  final VoidCallback? onAction;
  const SectionHeader({super.key, required this.title, this.action, this.onAction});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 18, 12, 8),
      child: Row(
        children: [
          Text(title,
              style: const TextStyle(color: kText, fontSize: 16, fontWeight: FontWeight.w700)),
          const Spacer(),
          if (action != null) TextButton(onPressed: onAction, child: Text(action!)),
        ],
      ),
    );
  }
}

class LuminusAvatar extends StatelessWidget {
  final double radius;
  final String? url;
  final String? letter;
  final bool ring;
  const LuminusAvatar({
    super.key,
    required this.radius,
    this.url,
    this.letter,
    this.ring = false,
  });

  Widget _initial() {
    return Container(
      color: kSurface2,
      alignment: Alignment.center,
      child: Text(
        (letter ?? '?').toUpperCase(),
        style: TextStyle(
          fontSize: radius * .8,
          fontWeight: FontWeight.w700,
          color: kText,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final resolved = url == null ? '' : ApiClient.mediaUrl(url!);
    final hasUrl = resolved.isNotEmpty;
    final avatar = ClipOval(
      child: SizedBox(
        width: radius * 2,
        height: radius * 2,
        child: hasUrl
            ? Image.network(
                resolved,
                fit: BoxFit.cover,
                errorBuilder: (_, _, _) => _initial(),
                loadingBuilder: (_, child, progress) =>
                    progress == null ? child : _initial(),
              )
            : _initial(),
      ),
    );
    if (!ring) return avatar;
    return Container(
      padding: const EdgeInsets.all(2),
      decoration: const BoxDecoration(
        shape: BoxShape.circle,
        gradient: LinearGradient(colors: [kPrimary, kAccent]),
      ),
      child: avatar,
    );
  }
}

class SkeletonBox extends StatefulWidget {
  final double? width;
  final double height;
  final double radius;
  const SkeletonBox({
    super.key,
    this.width,
    required this.height,
    this.radius = 16,
  });

  @override
  State<SkeletonBox> createState() => _SkeletonBoxState();
}

class _SkeletonBoxState extends State<SkeletonBox>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
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
      builder: (_, _) => Container(
        width: widget.width ?? double.infinity,
        height: widget.height,
        decoration: BoxDecoration(
          color: Color.lerp(kSurface2, kBorder, _controller.value) ?? kSurface2,
          borderRadius: BorderRadius.circular(widget.radius),
          border: Border.all(color: kBorder.withValues(alpha: .5)),
        ),
      ),
    );
  }
}

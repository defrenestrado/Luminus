import 'package:flutter/material.dart';

import 'theme.dart';

class StoryText extends StatelessWidget {
  final String text;
  final TextStyle style;
  final bool streaming;

  const StoryText({
    super.key,
    required this.text,
    required this.style,
    this.streaming = false,
  });

  static final _pattern = RegExp(
    r'\*\*\*([^*\n]+)\*\*\*|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*',
  );

  List<TextSpan> _spans() {
    final spans = <TextSpan>[];
    var cursor = 0;
    for (final match in _pattern.allMatches(text)) {
      if (match.start > cursor) {
        spans.add(TextSpan(text: text.substring(cursor, match.start)));
      }
      final strongItalic = match.group(1);
      final strong = match.group(2);
      final italic = match.group(3);
      if (strongItalic != null) {
        spans.add(TextSpan(
          text: strongItalic,
          style: const TextStyle(
            fontWeight: FontWeight.w700,
            fontStyle: FontStyle.italic,
          ),
        ));
      } else if (strong != null) {
        spans.add(TextSpan(
          text: strong,
          style: const TextStyle(fontWeight: FontWeight.w700),
        ));
      } else if (italic != null) {
        spans.add(TextSpan(
          text: italic,
          style: const TextStyle(fontStyle: FontStyle.italic),
        ));
      }
      cursor = match.end;
    }
    if (cursor < text.length) {
      spans.add(TextSpan(text: text.substring(cursor)));
    }
    return spans;
  }

  @override
  Widget build(BuildContext context) {
    final spans = _spans();
    return Text.rich(
      TextSpan(
        style: style,
        children: [
          ...spans,
          if (streaming)
            const TextSpan(
              text: '▍',
              style: TextStyle(
                color: kAccent,
                fontFamily: kSans,
                fontWeight: FontWeight.w700,
              ),
            ),
        ],
      ),
    );
  }
}

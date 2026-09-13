/// Wire Flutter's error surfaces into [Telemetry].
library;

import 'dart:async';
import 'dart:ui' show PlatformDispatcher;

import 'package:flutter/widgets.dart';

import 'facade.dart';

/// Install the four client error surfaces so every uncaught failure is reported:
/// build/layout errors, async platform errors, and (optionally) a recoverable
/// error-widget. Call after [Telemetry.init]. Framework error presentation is
/// preserved (we still call `presentError`).
void installErrorHandlers({
  Widget Function(FlutterErrorDetails details)? errorWidgetBuilder,
}) {
  final priorOnError = FlutterError.onError;
  FlutterError.onError = (FlutterErrorDetails details) {
    Telemetry.error(
      details.exception,
      stack: details.stack,
      message: details.exceptionAsString(),
    );
    if (priorOnError != null) {
      priorOnError(details);
    } else {
      FlutterError.presentError(details);
    }
  };

  PlatformDispatcher.instance.onError = (Object error, StackTrace stack) {
    Telemetry.error(error, stack: stack);
    return true; // handled — don't crash the isolate
  };

  if (errorWidgetBuilder != null) {
    ErrorWidget.builder = errorWidgetBuilder;
  }
}

/// Run [body] (typically `runApp(...)`) inside a guarded zone so uncaught async
/// errors in app code are reported. Ensures the binding is initialized in-zone.
void runGuarded(void Function() body) {
  runZonedGuarded<void>(
    () {
      WidgetsFlutterBinding.ensureInitialized();
      body();
    },
    (Object error, StackTrace stack) => Telemetry.error(error, stack: stack),
  );
}

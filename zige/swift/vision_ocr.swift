// Minimal Vision-framework OCR bridge for macOS.
// Usage: vision_ocr <image-path> [--lang zh-Hans,en-US] [--level accurate|fast]
//
// Output: one line per recognized text observation:
//   <x>\t<y>\t<w>\t<h>\t<text>
// Coordinates are normalized (0..1) with a bottom-left origin, matching
// Vision's coordinate space. The Python side converts them to pixels.
//
// Vision's Chinese model occasionally fails to load ("Error loading network
// -5"), which aborts the whole request. To keep the CLI usable we walk a
// fallback chain (accurate -> fast -> language auto-detection) and only fail
// once every configuration has been tried.

import Foundation
import Vision
import AppKit

func fail(_ message: String) -> Never {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
    exit(2)
}

func note(_ message: String) {
    FileHandle.standardError.write((message + "\n").data(using: .utf8)!)
}

let cliArgs = CommandLine.arguments

var languages = ["zh-Hans", "en-US"]
var forcedLevel: String? = nil
var imagePath: String? = nil

var i = 1
while i < cliArgs.count {
    let arg = cliArgs[i]
    if arg == "--lang", i + 1 < cliArgs.count {
        languages = cliArgs[i + 1].split(separator: ",").map(String.init)
        i += 2
    } else if arg == "--level", i + 1 < cliArgs.count {
        forcedLevel = cliArgs[i + 1]
        i += 2
    } else {
        imagePath = arg
        i += 1
    }
}

guard let path = imagePath else {
    fail("usage: vision_ocr <image-path> [--lang zh-Hans,en-US] [--level accurate|fast]")
}
guard let image = NSImage(contentsOfFile: path) else { fail("cannot load image: \(path)") }
guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let cgImage = bitmap.cgImage else { fail("cannot decode image: \(path)") }

struct Attempt {
    let level: VNRequestTextRecognitionLevel
    let languages: [String]?
    let label: String
}

var attempts: [Attempt] = []
if let forced = forcedLevel {
    attempts = [
        Attempt(
            level: forced == "fast" ? .fast : .accurate,
            languages: languages,
            label: forced
        )
    ]
} else {
    attempts = [
        Attempt(level: .accurate, languages: languages, label: "accurate+lang"),
        Attempt(level: .fast, languages: languages, label: "fast+lang"),
        Attempt(level: .accurate, languages: nil, label: "accurate+auto"),
    ]
}

func recognise(_ attempt: Attempt) throws -> [VNRecognizedTextObservation] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = attempt.level
    request.usesLanguageCorrection = false
    if let langs = attempt.languages {
        request.recognitionLanguages = langs
    }
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])
    return request.results ?? []
}

var results: [VNRecognizedTextObservation] = []
var succeeded = false
for attempt in attempts {
    do {
        results = try recognise(attempt)
        succeeded = true
        if attempt.label != attempts[0].label {
            note("vision_ocr: fell back to \(attempt.label)")
        }
        break
    } catch {
        note("vision_ocr: \(attempt.label) failed: \(error.localizedDescription)")
    }
}

if !succeeded {
    fail("vision request failed: every recognition configuration was rejected")
}

for observation in results {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox
    // Tabs/newlines inside a string would break the TSV contract.
    let text = candidate.string
        .replacingOccurrences(of: "\t", with: " ")
        .replacingOccurrences(of: "\n", with: " ")
    let line = String(
        format: "%.5f\t%.5f\t%.5f\t%.5f\t%@\n",
        box.minX, box.minY, box.width, box.height, text
    )
    FileHandle.standardOutput.write(line.data(using: .utf8)!)
}

// SPDX-License-Identifier: GPL-3.0-or-later
// personseg: mate de PERSONAS (Vision de macOS, calidad 'accurate') -> PNG gris 8 bit (255 = persona).
// Uso: personseg IMAGEN.jpg SALIDA.png   (stdout: JSON {"w":..,"h":..} del mate)
import Foundation
import Vision
import CoreImage
import CoreVideo

let args = CommandLine.arguments
guard args.count > 2 else { print("{\"error\":\"uso: personseg in.jpg out.png\"}"); exit(1) }
let url = URL(fileURLWithPath: args[1])
let handler = VNImageRequestHandler(url: url, options: [:])
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8
do { try handler.perform([req]) } catch { print("{\"error\":\"\(error)\"}"); exit(2) }
guard let pb = req.results?.first?.pixelBuffer else { print("{\"error\":\"sin resultado\"}"); exit(3) }
let ci = CIImage(cvPixelBuffer: pb)
let ctx = CIContext()
let cs = CGColorSpace(name: CGColorSpace.linearGray)!
do {
    try ctx.writePNGRepresentation(of: ci, to: URL(fileURLWithPath: args[2]), format: .L8, colorSpace: cs)
} catch { print("{\"error\":\"\(error)\"}"); exit(4) }
print("{\"w\":\(CVPixelBufferGetWidth(pb)),\"h\":\(CVPixelBufferGetHeight(pb))}")

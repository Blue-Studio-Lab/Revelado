// SPDX-License-Identifier: GPL-3.0-or-later
// facelm: caras con TODOS los puntos de referencia de Vision (76 pts) para el retoque editorial.
// Uso: facelm IMAGEN.jpg -> JSON en stdout; coordenadas normalizadas, origen ARRIBA-izquierda.
import Foundation
import Vision

let args = CommandLine.arguments
guard args.count > 1 else { print("{\"error\":\"uso\"}"); exit(1) }
let url = URL(fileURLWithPath: args[1])
let handler = VNImageRequestHandler(url: url, options: [:])
let req = VNDetectFaceLandmarksRequest()
do { try handler.perform([req]) } catch { print("{\"error\":\"\(error)\"}"); exit(2) }
func pts(_ r: VNFaceLandmarkRegion2D?, _ bb: CGRect) -> [[Double]] {
    guard let r = r else { return [] }
    return r.normalizedPoints.map { p in
        [Double(bb.origin.x + p.x * bb.size.width), 1.0 - Double(bb.origin.y + p.y * bb.size.height)]
    }
}
var faces: [[String: Any]] = []
for f in req.results ?? [] {
    let bb = f.boundingBox
    var d: [String: Any] = ["box": [Double(bb.origin.x), Double(1 - bb.origin.y - bb.size.height),
                                    Double(bb.size.width), Double(bb.size.height)], "conf": Double(f.confidence)]
    if let v = f.yaw { d["yaw"] = v.doubleValue }
    if let v = f.roll { d["roll"] = v.doubleValue }
    if #available(macOS 12.0, *) { if let v = f.pitch { d["pitch"] = v.doubleValue } }
    if let lm = f.landmarks {
        d["leftEye"] = pts(lm.leftEye, bb); d["rightEye"] = pts(lm.rightEye, bb)
        d["leftEyebrow"] = pts(lm.leftEyebrow, bb); d["rightEyebrow"] = pts(lm.rightEyebrow, bb)
        d["leftPupil"] = pts(lm.leftPupil, bb); d["rightPupil"] = pts(lm.rightPupil, bb)
        d["outerLips"] = pts(lm.outerLips, bb); d["innerLips"] = pts(lm.innerLips, bb)
        d["nose"] = pts(lm.nose, bb); d["noseCrest"] = pts(lm.noseCrest, bb)
        d["medianLine"] = pts(lm.medianLine, bb); d["faceContour"] = pts(lm.faceContour, bb)
    }
    faces.append(d)
}
let data = try! JSONSerialization.data(withJSONObject: ["faces": faces])
print(String(data: data, encoding: .utf8)!)

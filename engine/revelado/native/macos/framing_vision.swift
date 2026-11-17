// SPDX-License-Identifier: GPL-3.0-or-later
// encuadre_vision: analisis para ENCUADRE con el framework Vision de macOS (ligero, sobre la imagen web 8 bit).
// Uso: encuadre_vision IMAGEN.jpg  -> JSON en stdout. Coordenadas normalizadas, origen ARRIBA-izquierda.
//   faces:    [{box:[x,y,w,h], conf, yaw, roll, eyes:[[x,y],[x,y]]}]
//   humans:   [[x,y,w,h,conf]]
//   bodies:   [{joint_name: [x,y,conf]}]      (pose de cuerpo: tobillos, rodillas, munecas, codos, cadera...)
//   hands:    [{joint_name: [x,y,conf]}]      (pose de mano: puntas, nudillos, muneca)
//   salient:  [[x,y,w,h,conf]]                (saliencia por atencion: objetos destacados)
import Foundation
import Vision
import CoreImage

let args = CommandLine.arguments
guard args.count > 1 else { print("{\"error\":\"uso\"}"); exit(1) }
let url = URL(fileURLWithPath: args[1])
let handler = VNImageRequestHandler(url: url, options: [:])
let faceReq = VNDetectFaceLandmarksRequest()
let humanReq = VNDetectHumanRectanglesRequest()
humanReq.upperBodyOnly = false
let bodyReq = VNDetectHumanBodyPoseRequest()
let handReq = VNDetectHumanHandPoseRequest()
handReq.maximumHandCount = 6
let salReq = VNGenerateAttentionBasedSaliencyImageRequest()
do { try handler.perform([faceReq, humanReq, bodyReq, handReq, salReq]) } catch { print("{\"error\":\"\(error)\"}"); exit(2) }

func flip(_ bb: CGRect) -> [Double] {
    return [Double(bb.origin.x), Double(1 - bb.origin.y - bb.size.height), Double(bb.size.width), Double(bb.size.height)]
}
func centroid(_ r: VNFaceLandmarkRegion2D?, _ bb: CGRect) -> [Double]? {
    guard let r = r, r.pointCount > 0 else { return nil }
    var sx = 0.0, sy = 0.0
    for p in r.normalizedPoints { sx += Double(bb.origin.x + p.x * bb.size.width); sy += Double(bb.origin.y + p.y * bb.size.height) }
    let n = Double(r.pointCount)
    return [sx / n, 1.0 - sy / n]
}
var faces: [[String: Any]] = []
for f in faceReq.results ?? [] {
    let bb = f.boundingBox
    var d: [String: Any] = ["box": flip(bb), "conf": Double(f.confidence)]
    if let yaw = f.yaw { d["yaw"] = yaw.doubleValue }
    if let roll = f.roll { d["roll"] = roll.doubleValue }
    if let lm = f.landmarks {
        var eyes: [[Double]] = []
        if let a = centroid(lm.leftPupil ?? lm.leftEye, bb) { eyes.append(a) }
        if let b = centroid(lm.rightPupil ?? lm.rightEye, bb) { eyes.append(b) }
        d["eyes"] = eyes
        if let n = centroid(lm.noseCrest ?? lm.nose, bb) { d["nose"] = n }
    }
    faces.append(d)
}
var humans: [[Double]] = []
for h in humanReq.results ?? [] { humans.append(flip(h.boundingBox) + [Double(h.confidence)]) }

var bodies: [[String: [Double]]] = []
for o in bodyReq.results ?? [] {
    var d: [String: [Double]] = [:]
    if let pts = try? o.recognizedPoints(.all) {
        for (k, p) in pts where p.confidence > 0.15 { d[k.rawValue.rawValue] = [Double(p.location.x), 1.0 - Double(p.location.y), Double(p.confidence)] }
    }
    bodies.append(d)
}
var hands: [[String: [Double]]] = []
for o in handReq.results ?? [] {
    var d: [String: [Double]] = [:]
    if let pts = try? o.recognizedPoints(.all) {
        for (k, p) in pts where p.confidence > 0.2 { d[k.rawValue.rawValue] = [Double(p.location.x), 1.0 - Double(p.location.y), Double(p.confidence)] }
    }
    hands.append(d)
}
var salient: [[Double]] = []
if let s = salReq.results?.first as? VNSaliencyImageObservation {
    for o in s.salientObjects ?? [] { salient.append(flip(o.boundingBox) + [Double(o.confidence)]) }
}
let out: [String: Any] = ["faces": faces, "humans": humans, "bodies": bodies, "hands": hands, "salient": salient]
let data = try! JSONSerialization.data(withJSONObject: out)
print(String(data: data, encoding: .utf8)!)

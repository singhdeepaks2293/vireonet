/* VireoNet traffic link for NeXt UI: a label at each end of a link.
 * Classic (non-module) script on purpose: NeXt's this.inherited() walks
 * arguments.caller, which strict-mode code (every ES module) forbids.
 * Loaded after next.min.js by frontend/src/pages/Topology.tsx. */
(function () {
  if (!window.nx || nx.path(window, "VireoTrafficLink")) return;
  nx.define("VireoTrafficLink", nx.graphic.Topology.Link, {
    properties: { sourcelabel: null, targetlabel: null },
    view: function (view) {
      view.content.push(
        { name: "source", type: "nx.graphic.Text", props: { "class": "vireo-link-label", "alignment-baseline": "text-after-edge", "text-anchor": "start" } },
        { name: "target", type: "nx.graphic.Text", props: { "class": "vireo-link-label", "alignment-baseline": "text-after-edge", "text-anchor": "end" } }
      );
      return view;
    },
    methods: {
      update: function () {
        this.inherited();
        var scale = this.stageScale();
        var line = this.line();
        var angle = line.angle();
        // On-screen length; short links have no room for two labels.
        var roomy = line.length() / scale > 120;
        line = line.pad(22 * scale, 22 * scale);
        var self = this;
        function place(name, text, p) {
          var el = self.view(name);
          el.set("x", p.x);
          el.set("y", p.y);
          el.set("text", roomy ? text || "" : "");
          el.set("transform", "rotate(" + angle + " " + p.x + "," + p.y + ")");
          el.setStyle("font-size", 11 * scale);
        }
        place("source", this.sourcelabel(), line.start);
        place("target", this.targetlabel(), line.end);
      }
    }
  });
})();

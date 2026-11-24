// 渲染买家复核预览与客服详情共用的交接内容。

/** 将结构化诉求、核实事实和对话片段显示为纯文本。 */
function renderTicketHandoff(panel, handoff, title) {
  panel.hidden = !handoff;
  panel.replaceChildren();
  if (!handoff) return;

  const heading = handoffElement("h4", title);
  const facts = handoffElement("dl");
  const values = handoffElement("ul");
  for (const fact of handoff.verified_facts) values.append(handoffElement("li", fact));
  const evidence = handoffElement("dd");
  evidence.append(values);
  facts.append(
    handoffElement("dt", "买家诉求"), handoffElement("dd", handoff.buyer_request),
    handoffElement("dt", "已核实事实"), evidence,
    handoffElement("dt", "待人工复核"), handoffElement("dd", handoff.review_task),
  );
  panel.append(heading, facts);
  if (handoff.turns.length) {
    const transcript = handoffElement("details");
    const turns = handoffElement("ol");
    for (const turn of handoff.turns) {
      const entry = handoffElement("li");
      entry.append(handoffElement("strong", turn.question), handoffElement("p", turn.answer));
      turns.append(entry);
    }
    transcript.append(handoffElement("summary", "查看随附对话"), turns);
    panel.append(transcript);
  }
}

/** 建立仅含文本内容的交接元素。 */
function handoffElement(tag, value = "") {
  const node = document.createElement(tag);
  node.textContent = value;
  return node;
}
